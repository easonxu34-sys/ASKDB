#!/usr/bin/env bash
# Stop only PID-checked repository services, legacy launchd jobs, and Funnel; preserve data.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local-deploy-common.sh"

LEGACY_RUN_DIR="$LOCAL_DEPLOY_REPO_ROOT/.local-deploy/run"
AGENT_PID_FILE="$LEGACY_RUN_DIR/agent.pid"
WEB_PID_FILE="$LEGACY_RUN_DIR/web.pid"
AGENT_COMMAND_MARKER="uvicorn main:app --host 127.0.0.1 --port $LOCAL_DEPLOY_AGENT_PORT"
WEB_COMMAND_MARKER="next/dist/bin/next start --hostname 127.0.0.1 --port $LOCAL_DEPLOY_WEB_PORT"
AGENT_PLIST="$LOCAL_DEPLOY_RUN_DIR/agent.plist"
WEB_PLIST="$LOCAL_DEPLOY_RUN_DIR/web.plist"
TAILSCALE_BIN="${TAILSCALE_BIN:-$(command -v tailscale || true)}"
if [[ -z "$TAILSCALE_BIN" && -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]]; then
  TAILSCALE_BIN=/Applications/Tailscale.app/Contents/MacOS/Tailscale
fi

local_deploy_stop_pidfile Web "$WEB_PID_FILE" "$WEB_COMMAND_MARKER"
local_deploy_stop_pidfile Agent "$AGENT_PID_FILE" "$AGENT_COMMAND_MARKER"
local_deploy_bootout_launchd "$LOCAL_DEPLOY_WEB_LABEL" "$WEB_PLIST"
local_deploy_bootout_launchd "$LOCAL_DEPLOY_AGENT_LABEL" "$AGENT_PLIST"

funnel_status=0
if [[ -n "$TAILSCALE_BIN" && -x "$TAILSCALE_BIN" ]]; then
  local_deploy_clear_funnel "$TAILSCALE_BIN" || funnel_status=$?
else
  printf '警告: 未找到 Tailscale CLI；本地服务已停止，请手动关闭 Funnel :%s。\n' "$LOCAL_DEPLOY_FUNNEL_PORT" >&2
  funnel_status=1
fi

printf '本机 Agent/Web 服务已停止；仓库 Wren 项目和 profile，以及 %s 中的语料、删除 journal、日志和密钥配置均已保留。\n' "$LOCAL_DEPLOY_DIR"
if (( funnel_status != 0 )); then
  printf '请检查 tailscale funnel status，再决定是否运行 tailscale funnel reset；reset 会清除当前设备的全部 Funnel 配置。\n' >&2
  exit 1
fi
printf 'Tailscale Funnel 的 :%s 公网入口已关闭。\n' "$LOCAL_DEPLOY_FUNNEL_PORT"
