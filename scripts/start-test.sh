#!/usr/bin/env bash
# Start the repository's named-tunnel test stack. Never source the env file.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env.docker"
COMPOSE=(docker compose --project-name askdb-local-test --project-directory "$REPO_ROOT" --file "$REPO_ROOT/compose.yaml" --env-file "$ENV_FILE")

fail() { printf '错误: %s\n' "$1" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || fail '未找到 Docker；请安装并启动 Docker Desktop。'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose 不可用；请安装 Docker Compose 插件。'
[[ -f "$ENV_FILE" ]] || fail '缺少 .env.docker；请从 .env.docker.example 复制并填写模型、加密与隧道配置。'

# Compose parses dotenv syntax and required substitutions; do not print secrets.
"${COMPOSE[@]}" config --quiet || fail '.env.docker 或 compose.yaml 无效；请检查必填配置。'
resolved_environment="$("${COMPOSE[@]}" config --environment)" || fail '无法读取 Compose 配置。'
model_key=''
encryption_key=''
tunnel_token=''
tunnel_hostname=''
while IFS= read -r setting; do
  case "$setting" in
    OPENAI_API_KEY=*) model_key="${setting#*=}" ;;
    ASKDB_SETTINGS_ENCRYPTION_KEY=*) encryption_key="${setting#*=}" ;;
    CLOUDFLARE_TUNNEL_TOKEN=*) tunnel_token="${setting#*=}" ;;
    CLOUDFLARE_TUNNEL_HOSTNAME=*) tunnel_hostname="${setting#*=}" ;;
  esac
done <<< "$resolved_environment"
[[ -n "${model_key//[[:space:]]/}" ]] || fail '请在 .env.docker 中填写 OPENAI_API_KEY。'
[[ -n "${encryption_key//[[:space:]]/}" ]] || fail '请在 .env.docker 中填写 ASKDB_SETTINGS_ENCRYPTION_KEY。'
[[ -n "${tunnel_token//[[:space:]]/}" ]] || fail '请在 .env.docker 中填写 CLOUDFLARE_TUNNEL_TOKEN。'
[[ "$tunnel_hostname" =~ ^([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,}$ ]] || fail 'CLOUDFLARE_TUNNEL_HOSTNAME 必须是 DNS 主机名，不包含协议、端口或路径。'
unset resolved_environment model_key encryption_key tunnel_token

docker info >/dev/null 2>&1 || fail 'Docker daemon 不可用；请启动 Docker Desktop 后重试。'
printf '构建 askdb-local-test 镜像并准备隧道镜像…\n'
"${COMPOSE[@]}" build || fail '镜像构建失败；尚未开始服务就绪等待。'
"${COMPOSE[@]}" pull tunnel || fail '隧道镜像拉取失败；尚未开始服务就绪等待。'

# One deadline covers all starts and probes, including stalled Docker commands.
# Avoid command substitution for Docker output: a child plugin could retain its
# stdout pipe after its parent CLI is terminated, preventing the shell returning.
READINESS_DEADLINE=$((SECONDS + 120))
READINESS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/askdb-readiness.XXXXXX")"
trap 'rm -rf -- "$READINESS_DIR"' EXIT

before_deadline() {
  local command_pid command_status
  (( SECONDS < READINESS_DEADLINE )) || return 124
  "$@" &
  command_pid=$!
  while kill -0 "$command_pid" 2>/dev/null; do
    if (( SECONDS >= READINESS_DEADLINE )); then
      # Only terminate the command launched here; never kill by name or port.
      kill -TERM "$command_pid" 2>/dev/null || true
      kill -KILL "$command_pid" 2>/dev/null || true
      wait "$command_pid" 2>/dev/null || true
      printf '共享的 120 秒启动/就绪期限已到。\n' >&2
      return 124
    fi
    sleep 1
  done
  if wait "$command_pid"; then command_status=0; else command_status=$?; fi
  (( SECONDS < READINESS_DEADLINE )) || return 124
  return "$command_status"
}

startup_failed() {
  printf '启动或就绪检查失败（%s）。服务可能仍在运行；请检查此项目的 Compose ps/logs，或运行 scripts/stop-test.sh。\n' "$1" >&2
  exit 1
}

printf '按依赖顺序启动并检查服务；共享就绪期限最多 120 秒…\n'
for service in agent web tunnel; do
  before_deadline "${COMPOSE[@]}" up --detach --no-build --no-deps "$service" || startup_failed "$service 启动失败或超时"
  while :; do
    before_deadline "${COMPOSE[@]}" ps --all --quiet "$service" > "$READINESS_DIR/id" || startup_failed "$service 状态查询失败或超时"
    container_id=''
    IFS= read -r container_id < "$READINESS_DIR/id" || true
    [[ -n "$container_id" ]] || startup_failed "$service 容器不存在"
    before_deadline docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" > "$READINESS_DIR/status" || startup_failed "$service 健康查询失败或超时"
    service_status=''
    IFS= read -r service_status < "$READINESS_DIR/status" || true
    if [[ "$service" == tunnel && "$service_status" == running ]] || [[ "$service" != tunnel && "$service_status" == healthy ]]; then
      break
    fi
    case "$service_status" in
      unhealthy|exited|dead|removing) startup_failed "$service 状态为 $service_status" ;;
    esac
    (( SECONDS < READINESS_DEADLINE )) || startup_failed "$service 等待超时"
    sleep 1
  done
done
printf '\n本机页面/健康检查: http://localhost:3000\n'
printf '公网测试及登录: https://%s\n' "$tunnel_hostname"
printf '本地健康检查通过；公网 DNS、路由与登录仍需通过 HTTPS 地址确认。\n'
printf '首次使用请按 README 在容器内交互创建管理员。停止: scripts/stop-test.sh（保留数据卷）。\n'
