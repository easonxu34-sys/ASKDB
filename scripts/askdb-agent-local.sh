#!/usr/bin/env bash
# Run an Agent CLI command against the local public deployment's persistent data.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local-deploy-common.sh"

AGENT_CLI="$LOCAL_DEPLOY_VENV_DIR/bin/askdb-agent"
[[ -x "$AGENT_CLI" ]] || {
  local_deploy_fail '本地 Agent 尚未打包；请先运行 scripts/start-test.sh。'
  exit 1
}
[[ $# -gt 0 ]] || {
  local_deploy_fail '请提供 Agent CLI 子命令，例如 auth init-admin。'
  exit 2
}

ASKDB_SETTINGS_ENCRYPTION_KEY="$(local_deploy_load_encryption_key)" || exit 1
export ASKDB_SETTINGS_ENCRYPTION_KEY
export ASKDB_SETTINGS_DB_PATH="$LOCAL_DEPLOY_DATA_DIR/model-settings.sqlite3"
export ASKDB_WREN_DATA_DIR="$LOCAL_DEPLOY_DATA_DIR/wren"
export WREN_HOME="$LOCAL_DEPLOY_DATA_DIR/wren-home"

cd "$LOCAL_DEPLOY_AGENT_DIR"
exec "$AGENT_CLI" "$@"
