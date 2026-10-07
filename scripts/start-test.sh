#!/usr/bin/env bash
# Package and run the public demo locally, then expose only Web via Funnel.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "$BASH_SOURCE")" && pwd)"
source "$SCRIPT_DIR/local-deploy-common.sh"
SOURCE_ROOT="$LOCAL_DEPLOY_REPO_ROOT"
AGENT_SOURCE_DIR="$SOURCE_ROOT/askdb-agent"
WEB_SOURCE_DIR="$SOURCE_ROOT/askdb-web"
AGENT_PID_FILE="$SOURCE_ROOT/.local-deploy/run/agent.pid"
WEB_PID_FILE="$SOURCE_ROOT/.local-deploy/run/web.pid"
AGENT_COMMAND_MARKER="uvicorn main:app --host 127.0.0.1 --port $LOCAL_DEPLOY_AGENT_PORT"
WEB_COMMAND_MARKER="next/dist/bin/next start --hostname 127.0.0.1 --port $LOCAL_DEPLOY_WEB_PORT"
AGENT_PLIST="$LOCAL_DEPLOY_RUN_DIR/agent.plist"
WEB_PLIST="$LOCAL_DEPLOY_RUN_DIR/web.plist"
AGENT_BOOTSTRAPPED=false
WEB_BOOTSTRAPPED=false
FUNNEL_CONFIGURED=false
NODE_BIN=""
TAILSCALE_BIN="$(command -v tailscale || true)"
if [[ -z "$TAILSCALE_BIN" && -x /Applications/Tailscale.app/Contents/MacOS/Tailscale ]]; then
  TAILSCALE_BIN=/Applications/Tailscale.app/Contents/MacOS/Tailscale
fi

fail() {
  local_deploy_fail "$1"
  exit 1
}

cleanup_failed_start() {
  local exit_status=$?
  if (( exit_status != 0 )); then
    if [[ "$WEB_BOOTSTRAPPED" == true ]]; then
      local_deploy_bootout_launchd "$LOCAL_DEPLOY_WEB_LABEL" "$WEB_PLIST" || true
    fi
    if [[ "$AGENT_BOOTSTRAPPED" == true ]]; then
      local_deploy_bootout_launchd "$LOCAL_DEPLOY_AGENT_LABEL" "$AGENT_PLIST" || true
    fi
    if [[ "$FUNNEL_CONFIGURED" == true ]]; then
      local_deploy_clear_funnel "$TAILSCALE_BIN" || true
    fi
    printf '启动未完成；本地日志位于 %s。\n' "$LOCAL_DEPLOY_LOG_DIR" >&2
  fi
}
trap cleanup_failed_start EXIT

for tool in uv pnpm node curl lsof launchctl python3 rsync; do
  command -v "$tool" >/dev/null 2>&1 || fail "未找到 $tool。"
done
[[ -n "$TAILSCALE_BIN" && -x "$TAILSCALE_BIN" ]] || fail '未找到 Tailscale CLI；请安装、登录并启用 CLI 集成。'
[[ -f "$AGENT_SOURCE_DIR/uv.lock" && -f "$WEB_SOURCE_DIR/pnpm-lock.yaml" ]] || fail '缺少 Agent 或 Web 锁文件。'

NODE_BIN="$(node -p 'process.execPath')"
node_major="$("$NODE_BIN" -p 'Number(process.versions.node.split(".")[0])')"
(( node_major >= 22 )) || fail '公网 Web 构建要求 Node.js 22 或更新版本。'
local_deploy_assert_stopped Agent "$AGENT_PID_FILE" "$AGENT_COMMAND_MARKER" || exit 1
local_deploy_assert_stopped Web "$WEB_PID_FILE" "$WEB_COMMAND_MARKER" || exit 1
local_deploy_assert_launchd_unloaded Agent "$LOCAL_DEPLOY_AGENT_LABEL" || exit 1
local_deploy_assert_launchd_unloaded Web "$LOCAL_DEPLOY_WEB_LABEL" || exit 1

mkdir -p -m 700 "$LOCAL_DEPLOY_DIR" "$LOCAL_DEPLOY_RUN_DIR" "$LOCAL_DEPLOY_LOG_DIR" "$LOCAL_DEPLOY_APP_ROOT"
chmod 700 "$LOCAL_DEPLOY_DIR" "$LOCAL_DEPLOY_RUN_DIR" "$LOCAL_DEPLOY_LOG_DIR" "$LOCAL_DEPLOY_APP_ROOT"

printf '将 Agent 与 Web 源码打包到用户级 Application Support…\n'
mkdir -p -m 700 "$LOCAL_DEPLOY_AGENT_DIR" "$LOCAL_DEPLOY_WEB_DIR" "$LOCAL_DEPLOY_APP_ROOT/scripts"
rsync -a --delete \
  --exclude='.env*' --exclude='.venv/' --exclude='data/' --exclude='__pycache__/' \
  --exclude='*.pyc' --exclude='.pytest_cache/' --exclude='.mypy_cache/' --exclude='.ruff_cache/' \
  "$AGENT_SOURCE_DIR/" "$LOCAL_DEPLOY_AGENT_DIR/"
rsync -a --delete \
  --exclude='.env*' --exclude='.git/' --exclude='node_modules/' --exclude='.next/' \
  --exclude='.turbo/' --exclude='.vercel/' \
  "$WEB_SOURCE_DIR/" "$LOCAL_DEPLOY_WEB_DIR/"
install -m 700 "$SCRIPT_DIR/run-local-agent.sh" "$LOCAL_DEPLOY_APP_ROOT/scripts/run-local-agent.sh"
install -m 700 "$SCRIPT_DIR/run-local-web.sh" "$LOCAL_DEPLOY_APP_ROOT/scripts/run-local-web.sh"
install -m 600 "$SCRIPT_DIR/local-deploy-common.sh" "$LOCAL_DEPLOY_APP_ROOT/scripts/local-deploy-common.sh"

printf '在本机按 uv.lock 安装 Agent 运行时（Python 3.13）…\n'
UV_PROJECT_ENVIRONMENT="$LOCAL_DEPLOY_VENV_DIR" uv sync --locked --no-dev --no-editable --python 3.13 --project "$LOCAL_DEPLOY_AGENT_DIR"
[[ -x "$LOCAL_DEPLOY_VENV_DIR/bin/uvicorn" && -x "$LOCAL_DEPLOY_VENV_DIR/bin/askdb-agent" ]] || fail 'Agent 本地安装未生成运行命令。'

sync_encryption_key() {
  local local_file="$SOURCE_ROOT/.env.local"
  local legacy_file="$SOURCE_ROOT/.env.docker"
  local agent_env_file="$AGENT_SOURCE_DIR/.env"
  local local_key="" legacy_key="" selected_key="" selected_dsn=""

  if [[ -f "$local_file" ]]; then
    local_key="$(local_deploy_read_key_from_file "$local_file")" || fail '.env.local 中的 ASKDB_SETTINGS_ENCRYPTION_KEY 无效。'
  fi
  if [[ -f "$legacy_file" ]]; then
    legacy_key="$(local_deploy_read_key_from_file "$legacy_file")" || fail '.env.docker 中的 ASKDB_SETTINGS_ENCRYPTION_KEY 无效。'
  fi
  if [[ -n "$local_key" && -n "$legacy_key" && "$local_key" != "$legacy_key" ]]; then
    fail '.env.local 与 .env.docker 的 Fernet key 不一致；迁移过来的加密配置无法解密。'
  fi
  selected_key="$local_key"
  [[ -n "$selected_key" ]] || selected_key="$legacy_key"
  if [[ -z "$selected_key" && -f "$LOCAL_DEPLOY_SECRET_FILE" ]]; then
    selected_key="$(local_deploy_load_encryption_key)" || exit 1
  fi
  [[ -n "$selected_key" ]] || fail '缺少 ASKDB_SETTINGS_ENCRYPTION_KEY；请配置 .env.local，旧部署可沿用 .env.docker。'
  if [[ -f "$LOCAL_DEPLOY_SECRET_FILE" ]]; then
    local active_key=""
    active_key="$(local_deploy_load_encryption_key)" || exit 1
    if [[ "$selected_key" != "$active_key" ]]; then
      fail '配置文件中的 Fernet key 与 Application Support 当前 key 不一致；为保护已加密配置，启动已停止。'
    fi
    unset active_key
  fi

  selected_dsn="${ASKDB_DATABASE_DSN:-}"
  if [[ -z "$selected_dsn" && -f "$agent_env_file" ]]; then
    selected_dsn="$(local_deploy_read_database_dsn_from_file "$agent_env_file")" || fail '无法读取 askdb-agent/.env 中的 PostgreSQL DSN。'
  fi
  if [[ -z "$selected_dsn" && -f "$LOCAL_DEPLOY_SECRET_FILE" ]]; then
    selected_dsn="$(local_deploy_read_database_dsn_from_file "$LOCAL_DEPLOY_SECRET_FILE")" || fail '无法读取 Application Support 中的 PostgreSQL DSN。'
  fi
  [[ -n "$selected_dsn" ]] || selected_dsn='service=askdb-agent-dev'
  [[ "$selected_dsn" != *$'\n'* ]] || fail 'ASKDB_DATABASE_DSN 不能包含换行。'

  local temp_file="$LOCAL_DEPLOY_DIR/.secrets.env.tmp.$$"
  ASKDB_LOCAL_SECRET_KEY="$selected_key" ASKDB_LOCAL_SECRET_DSN="$selected_dsn" \
    "$LOCAL_DEPLOY_VENV_DIR/bin/python" - "$temp_file" <<'PY'
import os
import sys

from dotenv import set_key

path = sys.argv[1]
set_key(path, "ASKDB_SETTINGS_ENCRYPTION_KEY", os.environ["ASKDB_LOCAL_SECRET_KEY"], quote_mode="always")
set_key(path, "ASKDB_DATABASE_DSN", os.environ["ASKDB_LOCAL_SECRET_DSN"], quote_mode="always")
PY
  chmod 600 "$temp_file"
  mv -f -- "$temp_file" "$LOCAL_DEPLOY_SECRET_FILE"
  unset local_key legacy_key selected_key selected_dsn
}
sync_encryption_key

printf '在本机按 pnpm-lock.yaml 安装并构建 Next.js Web…\n'
(cd "$LOCAL_DEPLOY_WEB_DIR" && pnpm install --frozen-lockfile && pnpm exec next build --webpack)

prepare_data_directory() {
  local source_data="$SOURCE_ROOT/.local-deploy/data"
  if [[ ! -d "$LOCAL_DEPLOY_DATA_DIR" || -z "$(find "$LOCAL_DEPLOY_DATA_DIR" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null)" ]]; then
    if [[ -d "$source_data" ]] && find "$source_data" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
      [[ ! -e "$LOCAL_DEPLOY_DATA_DIR" ]] || rmdir "$LOCAL_DEPLOY_DATA_DIR"
      mv -- "$source_data" "$LOCAL_DEPLOY_DATA_DIR"
      printf '已将先前迁移的数据目录移入 Application Support。\n'
    fi
  fi
  mkdir -p -m 700 "$LOCAL_DEPLOY_DATA_DIR" "$LOCAL_DEPLOY_DATA_DIR/wren" "$LOCAL_DEPLOY_DATA_DIR/wren-home"
  chmod 700 "$LOCAL_DEPLOY_DATA_DIR" "$LOCAL_DEPLOY_DATA_DIR/wren" "$LOCAL_DEPLOY_DATA_DIR/wren-home"
}
prepare_data_directory

for port in "$LOCAL_DEPLOY_AGENT_PORT" "$LOCAL_DEPLOY_WEB_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN 2>/dev/null | tail -n +2 | grep -q .; then
    fail "本地端口 $port 已被占用；脚本不会按端口杀进程，请先识别并停止占用者。"
  fi
done

printf '配置 Tailscale Funnel（HTTPS :%s → 127.0.0.1:%s）…\n' "$LOCAL_DEPLOY_FUNNEL_PORT" "$LOCAL_DEPLOY_WEB_PORT"
env TAILSCALE_BE_CLI=1 "$TAILSCALE_BIN" funnel --https="$LOCAL_DEPLOY_FUNNEL_PORT" --bg "$LOCAL_DEPLOY_WEB_PORT" > "$LOCAL_DEPLOY_DIR/funnel.log" 2>&1 || {
  cat "$LOCAL_DEPLOY_DIR/funnel.log" >&2
  fail 'Tailscale Funnel 启动失败；请确认已登录、启用 MagicDNS/HTTPS 并允许 Funnel。'
}
FUNNEL_CONFIGURED=true
chmod 600 "$LOCAL_DEPLOY_DIR/funnel.log"
FUNNEL_URL="$(sed -nE 's/^[[:space:]]*(https:\/\/[^[:space:]]+).*$/\1/p' "$LOCAL_DEPLOY_DIR/funnel.log" | head -n 1)"
[[ "$FUNNEL_URL" =~ ^https://[A-Za-z0-9.-]+:8443/?$ ]] || fail '无法从 Tailscale CLI 输出中取得 HTTPS :8443 公网地址。'
ASKDB_WEB_ORIGIN="$(printf '%s' "$FUNNEL_URL" | sed 's:/*$::')"

for log_file in agent.stdout.log agent.stderr.log web.stdout.log web.stderr.log; do
  touch "$LOCAL_DEPLOY_LOG_DIR/$log_file"
  chmod 600 "$LOCAL_DEPLOY_LOG_DIR/$log_file"
done
python3 "$SCRIPT_DIR/write-local-launchd-plists.py" \
  "$LOCAL_DEPLOY_APP_ROOT" "$LOCAL_DEPLOY_RUN_DIR" "$LOCAL_DEPLOY_LOG_DIR" "$ASKDB_WEB_ORIGIN" "$NODE_BIN"

printf '通过 launchd 启动 Agent（127.0.0.1:%s）…\n' "$LOCAL_DEPLOY_AGENT_PORT"
launchctl bootstrap "$LOCAL_DEPLOY_LAUNCHD_DOMAIN" "$AGENT_PLIST"
AGENT_BOOTSTRAPPED=true
agent_ready=false
for _ in {1..120}; do
  if curl --fail --silent --show-error --max-time 2 "http://127.0.0.1:$LOCAL_DEPLOY_AGENT_PORT/healthz" >/dev/null 2>&1; then
    agent_ready=true
    break
  fi
  sleep 1
done
[[ "$agent_ready" == true ]] || fail "Agent 未在 120 秒内就绪；请检查 $LOCAL_DEPLOY_LOG_DIR/agent.stderr.log。"

printf '通过 launchd 启动 Web（127.0.0.1:%s）…\n' "$LOCAL_DEPLOY_WEB_PORT"
launchctl bootstrap "$LOCAL_DEPLOY_LAUNCHD_DOMAIN" "$WEB_PLIST"
WEB_BOOTSTRAPPED=true

web_ready=false
for _ in {1..120}; do
  if curl --fail --silent --show-error --max-time 2 "http://127.0.0.1:$LOCAL_DEPLOY_WEB_PORT/" >/dev/null 2>&1; then
    web_ready=true
    break
  fi
  sleep 1
done
[[ "$web_ready" == true ]] || fail "Web 未在 120 秒内就绪；请检查 $LOCAL_DEPLOY_LOG_DIR/web.stderr.log。"

printf '\n本机健康检查: http://127.0.0.1:%s/healthz（Agent）、http://127.0.0.1:%s/（Web）\n' "$LOCAL_DEPLOY_AGENT_PORT" "$LOCAL_DEPLOY_WEB_PORT"
printf '公网登录及使用: %s\n' "$ASKDB_WEB_ORIGIN"
printf '本地构建和健康检查通过；Agent/Web 由当前用户的 launchd 后台托管，关闭终端后仍会运行。公网登录及 SSE 聊天仍需浏览器实测确认。\n'
printf '管理员初始化命令: scripts/askdb-agent-local.sh auth init-admin\n'
printf '停止服务与公网入口: scripts/stop-test.sh（保留 %s/data）。\n' "$LOCAL_DEPLOY_DIR"
