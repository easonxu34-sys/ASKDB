#!/usr/bin/env bash
# Stop only this Compose project and its Funnel port; keep the named volume.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env.docker"
source "$REPO_ROOT/scripts/local-tunnel.sh"
TAILSCALE_BIN="${TAILSCALE_BIN:-$(command -v tailscale || true)}"
if [[ -z "$TAILSCALE_BIN" && -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]]; then
  TAILSCALE_BIN=/Applications/Tailscale.app/Contents/MacOS/Tailscale
fi
FUNNEL_HTTPS_PORT=8443
LOCAL_WEB_PORT=3001
fail() { printf '错误: %s\n' "$1" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || fail '未找到 Docker；请安装并启动 Docker Desktop。'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose 不可用。'
[[ -f "$ENV_FILE" ]] || fail '缺少 .env.docker；请恢复该项目的配置文件后停止。'
docker info >/dev/null 2>&1 || fail 'Docker daemon 不可用；请启动 Docker Desktop 后重试。'

funnel_status=0
if [[ -n "$TAILSCALE_BIN" && -x "$TAILSCALE_BIN" ]]; then
  env TAILSCALE_BE_CLI=1 "$TAILSCALE_BIN" funnel --https="$FUNNEL_HTTPS_PORT" --bg "$LOCAL_WEB_PORT" off || funnel_status=$?
else
  printf '警告: 未找到 Tailscale CLI；Docker 服务会停止，但 Funnel 配置需用 Tailscale CLI 手动关闭。\n' >&2
  funnel_status=1
fi

docker compose --project-name askdb-local-test --project-directory "$REPO_ROOT" \
  --file "$REPO_ROOT/compose.yaml" --env-file "$ENV_FILE" down
local_tunnel_stop
printf 'askdb-local-test 已停止；agent_data 数据卷已保留。\n'
if (( funnel_status != 0 )); then
  printf '请在 Tailscale CLI 执行: tailscale funnel --https=%s --bg %s off\n' "$FUNNEL_HTTPS_PORT" "$LOCAL_WEB_PORT" >&2
  exit 1
fi
printf 'Tailscale Funnel 的 :%s 公网入口已关闭。\n' "$FUNNEL_HTTPS_PORT"
