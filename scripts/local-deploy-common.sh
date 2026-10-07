#!/usr/bin/env bash
# Shared paths and guarded process helpers for the local public deployment.

LOCAL_DEPLOY_SCRIPTS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_DEPLOY_REPO_ROOT="$(cd -- "$LOCAL_DEPLOY_SCRIPTS_DIR/.." && pwd)"
LOCAL_DEPLOY_DIR="${ASKDB_LOCAL_DEPLOY_DIR:-$HOME/Library/Application Support/ASKDB-Agent}"
LOCAL_DEPLOY_APP_ROOT="$LOCAL_DEPLOY_DIR/app"
LOCAL_DEPLOY_AGENT_DIR="$LOCAL_DEPLOY_APP_ROOT/askdb-agent"
LOCAL_DEPLOY_WEB_DIR="$LOCAL_DEPLOY_APP_ROOT/askdb-web"
LOCAL_DEPLOY_DATA_DIR="$LOCAL_DEPLOY_DIR/data"
LOCAL_DEPLOY_RUN_DIR="$LOCAL_DEPLOY_DIR/run"
LOCAL_DEPLOY_LOG_DIR="$LOCAL_DEPLOY_DIR/logs"
LOCAL_DEPLOY_VENV_DIR="$LOCAL_DEPLOY_DIR/venv"
LOCAL_DEPLOY_SECRET_FILE="$LOCAL_DEPLOY_DIR/secrets.env"
LOCAL_DEPLOY_AGENT_PORT=8001
LOCAL_DEPLOY_WEB_PORT=3001
LOCAL_DEPLOY_FUNNEL_PORT=8443
LOCAL_DEPLOY_AGENT_LABEL="com.askdb.local-public.agent"
LOCAL_DEPLOY_WEB_LABEL="com.askdb.local-public.web"
LOCAL_DEPLOY_LAUNCHD_DOMAIN="gui/$(id -u)"

local_deploy_fail() {
  printf '错误: %s\n' "$1" >&2
  return 1
}

local_deploy_read_key_from_file() {
  local env_file="$1"
  "${LOCAL_DEPLOY_VENV_DIR}/bin/python" - "$env_file" <<'PY'
import sys

from cryptography.fernet import Fernet
from dotenv import dotenv_values

path = sys.argv[1]
value = dotenv_values(path).get("ASKDB_SETTINGS_ENCRYPTION_KEY")
key = value.strip() if isinstance(value, str) else ""
if key:
    try:
        Fernet(key.encode("ascii"))
    except Exception:
        raise SystemExit(2)
print(key, end="")
PY
}

local_deploy_load_encryption_key() {
  [[ -f "$LOCAL_DEPLOY_SECRET_FILE" ]] || {
    local_deploy_fail '缺少本地部署密钥；请从仓库运行 scripts/start-test.sh 完成本机打包。'
    return 1
  }
  local key=""
  key="$(local_deploy_read_key_from_file "$LOCAL_DEPLOY_SECRET_FILE")" || {
    local_deploy_fail 'Application Support 中的 ASKDB_SETTINGS_ENCRYPTION_KEY 无效。'
    return 1
  }
  printf '%s' "$key"
}

local_deploy_read_database_dsn_from_file() {
  local env_file="$1"
  "${LOCAL_DEPLOY_VENV_DIR}/bin/python" - "$env_file" <<'PY'
import sys

from dotenv import dotenv_values

value = dotenv_values(sys.argv[1]).get("ASKDB_DATABASE_DSN")
dsn = value.strip() if isinstance(value, str) else ""
print(dsn, end="")
PY
}

local_deploy_load_database_dsn() {
  local dsn="${ASKDB_DATABASE_DSN:-}"
  if [[ -z "$dsn" && -f "$LOCAL_DEPLOY_SECRET_FILE" ]]; then
    dsn="$(local_deploy_read_database_dsn_from_file "$LOCAL_DEPLOY_SECRET_FILE")" || {
      local_deploy_fail '无法读取 Application Support 中的 PostgreSQL DSN。'
      return 1
    }
  fi
  [[ -n "$dsn" ]] || {
    local_deploy_fail '缺少 PostgreSQL DSN；请在 askdb-agent/.env 设置 ASKDB_DATABASE_DSN 并重新运行 scripts/start-test.sh。'
    return 1
  }
  printf '%s' "$dsn"
}

local_deploy_process_matches() {
  local pid="$1" expected_command="$2" process_command=""
  [[ "$pid" =~ ^[0-9]+$ ]] || return 1
  process_command="$(ps -p "$pid" -o command= 2>/dev/null || true)"
  [[ -n "$process_command" && "$process_command" == *"$expected_command"* ]]
}

local_deploy_assert_stopped() {
  local name="$1" pid_file="$2" expected_command="$3" pid=""
  [[ -f "$pid_file" ]] || return 0
  IFS= read -r pid < "$pid_file" || true
  if local_deploy_process_matches "$pid" "$expected_command"; then
    local_deploy_fail "$name 已由本地部署脚本启动（PID $pid）；请先运行 scripts/stop-test.sh。"
    return 1
  fi
  rm -f -- "$pid_file"
}

local_deploy_stop_pidfile() {
  local name="$1" pid_file="$2" expected_command="$3" pid="" attempt=0
  [[ -f "$pid_file" ]] || return 0
  IFS= read -r pid < "$pid_file" || true
  if ! local_deploy_process_matches "$pid" "$expected_command"; then
    rm -f -- "$pid_file"
    printf '%s 未运行或 PID 已变化；未向该 PID 发送信号。\n' "$name"
    return 0
  fi

  kill -TERM "$pid" 2>/dev/null || true
  while (( attempt < 10 )) && kill -0 "$pid" 2>/dev/null; do
    sleep 1
    ((attempt += 1))
  done
  if kill -0 "$pid" 2>/dev/null && local_deploy_process_matches "$pid" "$expected_command"; then
    kill -KILL "$pid" 2>/dev/null || true
  fi
  rm -f -- "$pid_file"
  printf '%s 已停止。\n' "$name"
}

local_deploy_launchd_loaded() {
  local label="$1"
  launchctl print "$LOCAL_DEPLOY_LAUNCHD_DOMAIN/$label" >/dev/null 2>&1
}

local_deploy_assert_launchd_unloaded() {
  local name="$1" label="$2"
  if local_deploy_launchd_loaded "$label"; then
    local_deploy_fail "$name 已由 launchd 加载；请先运行 scripts/stop-test.sh。"
    return 1
  fi
}

local_deploy_bootout_launchd() {
  local label="$1" plist_path="$2"
  if ! local_deploy_launchd_loaded "$label"; then return 0; fi
  if [[ -f "$plist_path" ]]; then
    launchctl bootout "$LOCAL_DEPLOY_LAUNCHD_DOMAIN" "$plist_path"
  else
    launchctl bootout "$LOCAL_DEPLOY_LAUNCHD_DOMAIN/$label"
  fi
}

local_deploy_clear_funnel() {
  local tailscale_bin="$1" status_json="" route_state=""
  status_json="$(env TAILSCALE_BE_CLI=1 "$tailscale_bin" funnel status --json)" || {
    local_deploy_fail '无法读取当前 Funnel 配置；为避免误关其他公网服务，未执行 reset。'
    return 1
  }
  route_state="$(python3 -c '
import json, sys
value = json.load(sys.stdin)
tcp = value.get("TCP") or {}
web = value.get("Web") or {}
allowed = value.get("AllowFunnel") or {}
if not tcp and not web:
    print("none")
    raise SystemExit(0)
owned_port = set(tcp) == {"8443"} and tcp.get("8443") == {"HTTPS": True}
owned_web = len(web) == 1
owned_host = next(iter(web), "")
owned_handler = web.get(owned_host, {}).get("Handlers") == {
    "/": {"Proxy": "http://127.0.0.1:3001"}
}
owned_allow = allowed == {owned_host: True}
if owned_port and owned_host.endswith(":8443") and owned_handler and owned_allow:
    print("owned")
else:
    print("other")
' <<< "$status_json")" || {
    local_deploy_fail '无法解析当前 Funnel 配置；为避免误关其他公网服务，未执行 reset。'
    return 1
  }
  case "$route_state" in
    none)
      printf 'Tailscale Funnel 当前没有活动配置。\n'
      return 0
      ;;
    owned)
      env TAILSCALE_BE_CLI=1 "$tailscale_bin" funnel reset
      ;;
    *)
      local_deploy_fail '发现非本部署拥有的 Funnel 配置；服务已停止，但未执行全局 Funnel reset。'
      return 1
      ;;
  esac
}
