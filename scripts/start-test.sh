#!/usr/bin/env bash
# Start the repository's named-tunnel test stack. Never source the env file.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env.docker"
COMPOSE=(docker compose --project-name askdb-local-test --project-directory "$REPO_ROOT" --file "$REPO_ROOT/compose.yaml" --env-file "$ENV_FILE")

fail() { printf '错误: %s\n' "$1" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || fail '未找到 Docker；请安装并启动 Docker Desktop。'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose 不可用；请安装支持 up --wait 的 Docker Compose。'
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
printf '构建并启动 askdb-local-test；构建完成后等待服务就绪（最多 120 秒）…\n'
if ! "${COMPOSE[@]}" up --detach --build --wait --wait-timeout 120; then
  printf '启动或就绪检查失败。服务可能仍在运行；请检查此项目的 Compose ps/logs，或运行 scripts/stop-test.sh。\n' >&2
  exit 1
fi
printf '\n本机页面/健康检查: http://localhost:3000\n'
printf '公网测试及登录: https://%s\n' "$tunnel_hostname"
printf '本地健康检查通过；公网 DNS、路由与登录仍需通过 HTTPS 地址确认。\n'
printf '首次使用请按 README 在容器内交互创建管理员。停止: scripts/stop-test.sh（保留数据卷）。\n'
