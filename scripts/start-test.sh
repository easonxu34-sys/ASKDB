#!/usr/bin/env bash
# Build and start the local Docker demo behind a persistent Tailscale Funnel.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env.docker"
COMPOSE=(docker compose --project-name askdb-local-test --project-directory "$REPO_ROOT" --file "$REPO_ROOT/compose.yaml" --env-file "$ENV_FILE")
source "$REPO_ROOT/scripts/local-tunnel.sh"
TAILSCALE_BIN="${TAILSCALE_BIN:-$(command -v tailscale || true)}"
if [[ -z "$TAILSCALE_BIN" && -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]]; then
  TAILSCALE_BIN=/Applications/Tailscale.app/Contents/MacOS/Tailscale
fi
FUNNEL_HTTPS_PORT=8443
LOCAL_WEB_PORT=3001

fail() { printf '错误: %s\n' "$1" >&2; exit 1; }
tailscale_cli() { env TAILSCALE_BE_CLI=1 "$TAILSCALE_BIN" "$@"; }
command -v docker >/dev/null 2>&1 || fail '未找到 Docker；请安装并启动 Docker Desktop。'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose 不可用；请安装 Docker Compose 插件。'
[[ -f "$ENV_FILE" ]] || fail '缺少 .env.docker；请从 .env.docker.example 复制并填写加密密钥。'
[[ -n "$TAILSCALE_BIN" && -x "$TAILSCALE_BIN" ]] || fail '未找到 Tailscale CLI。请先安装并登录 Tailscale，并在客户端设置中启用 CLI 集成。'

# Compose parses dotenv syntax and required substitutions; do not print secrets.
"${COMPOSE[@]}" config --quiet || fail '.env.docker 或 compose.yaml 无效；请检查必填配置。'
resolved_environment="$("${COMPOSE[@]}" config --environment)" || fail '无法读取 Compose 配置。'
encryption_key=''
while IFS= read -r setting; do
  case "$setting" in
    ASKDB_SETTINGS_ENCRYPTION_KEY=*) encryption_key="${setting#*=}" ;;
  esac
done <<< "$resolved_environment"
[[ -n "${encryption_key//[[:space:]]/}" ]] || fail '请在 .env.docker 中填写 ASKDB_SETTINGS_ENCRYPTION_KEY。'
unset resolved_environment encryption_key

docker info >/dev/null 2>&1 || fail 'Docker daemon 不可用；请启动 Docker Desktop 后重试。'
printf '构建 askdb-local-test 的 Agent 与 Web 镜像…\n'
"${COMPOSE[@]}" build || fail '镜像构建失败；尚未开始服务就绪等待。'

# One deadline covers service starts, health checks, and Funnel setup after build.
READINESS_DEADLINE=$((SECONDS + 120))
READINESS_DIR="$(mktemp -d "${TMPDIR:-/tmp}/askdb-readiness.XXXXXX")"
FUNNEL_STARTED_BY_THIS_RUN=false
cleanup_start() {
  local exit_status=$?
  rm -rf -- "$READINESS_DIR"
  if (( exit_status != 0 )) && [[ "$FUNNEL_STARTED_BY_THIS_RUN" == true ]]; then
    tailscale_cli funnel --https="$FUNNEL_HTTPS_PORT" --bg "$LOCAL_WEB_PORT" off >/dev/null 2>&1 || true
  fi
}
trap cleanup_start EXIT

before_deadline() {
  local command_pid command_status
  (( SECONDS < READINESS_DEADLINE )) || return 124
  "$@" &
  command_pid=$!
  while kill -0 "$command_pid" 2>/dev/null; do
    if (( SECONDS >= READINESS_DEADLINE )); then
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

wait_for_service() {
  local service="$1" container_id='' service_status=''
  while :; do
    before_deadline "${COMPOSE[@]}" ps --all --quiet "$service" > "$READINESS_DIR/id" || startup_failed "$service 状态查询失败或超时"
    IFS= read -r container_id < "$READINESS_DIR/id" || true
    [[ -n "$container_id" ]] || startup_failed "$service 容器不存在"
    before_deadline docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" > "$READINESS_DIR/status" || startup_failed "$service 健康查询失败或超时"
    IFS= read -r service_status < "$READINESS_DIR/status" || true
    [[ "$service_status" == healthy ]] && return 0
    case "$service_status" in
      unhealthy|exited|dead|removing) startup_failed "$service 状态为 $service_status" ;;
    esac
    (( SECONDS < READINESS_DEADLINE )) || startup_failed "$service 等待超时"
    sleep 1
  done
}

printf '启动并等待 Agent 就绪…\n'
before_deadline "${COMPOSE[@]}" up --detach --no-build --no-deps agent || startup_failed 'Agent 启动失败或超时'
wait_for_service agent

printf '启用 Tailscale Funnel（HTTPS :%s → localhost:%s）…\n' "$FUNNEL_HTTPS_PORT" "$LOCAL_WEB_PORT"
before_deadline env TAILSCALE_BE_CLI=1 "$TAILSCALE_BIN" funnel --https="$FUNNEL_HTTPS_PORT" --bg "$LOCAL_WEB_PORT" > "$READINESS_DIR/funnel" 2>&1 || {
  cat "$READINESS_DIR/funnel" >&2
  startup_failed 'Tailscale Funnel 启动失败；请确认已登录、启用 MagicDNS/HTTPS 并允许 Funnel'
}
FUNNEL_STARTED_BY_THIS_RUN=true
cat "$READINESS_DIR/funnel"
FUNNEL_URL="$(sed -nE 's/^[[:space:]]*(https:\/\/[^[:space:]]+).*$/\1/p' "$READINESS_DIR/funnel" | head -n 1)"
[[ "$FUNNEL_URL" =~ ^https://[A-Za-z0-9.-]+:8443/?$ ]] || startup_failed '未能从 Tailscale CLI 输出中取得 HTTPS 公网地址'
ASKDB_WEB_ORIGIN="${FUNNEL_URL%/}"
export ASKDB_WEB_ORIGIN

printf '启动 Web 并等待就绪…\n'
before_deadline "${COMPOSE[@]}" up --detach --no-build --no-deps --force-recreate web || startup_failed 'Web 启动失败或超时'
wait_for_service web
local_tunnel_stop

printf '\n本机页面/健康检查: http://localhost:%s\n' "$LOCAL_WEB_PORT"
printf '公网测试及登录: %s\n' "$ASKDB_WEB_ORIGIN"
printf '本地健康检查通过；Tailscale Funnel 在后台运行，无需保持此终端打开。公网登录和 SSE 聊天仍需用浏览器实测确认。\n'
printf '首次使用请另开终端按 README 初始化管理员。停止服务与公网入口: scripts/stop-test.sh（保留数据卷）。\n'
