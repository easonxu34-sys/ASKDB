#!/usr/bin/env bash
# Run an Agent CLI command against the repository-backed local deployment.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local-deploy-common.sh"

LOCAL_DEPLOY_AGENT_DIR="$LOCAL_DEPLOY_REPO_ROOT/askdb-agent"
LOCAL_DEPLOY_VENV_DIR="$LOCAL_DEPLOY_AGENT_DIR/.venv"
AGENT_CLI="$LOCAL_DEPLOY_VENV_DIR/bin/askdb-agent"
[[ -x "$AGENT_CLI" ]] || {
  local_deploy_fail '仓库 Agent 虚拟环境尚未准备；请先运行 scripts/start-test.sh。'
  exit 1
}
[[ $# -gt 0 ]] || {
  local_deploy_fail '请提供 Agent CLI 子命令，例如 auth init-admin。'
  exit 2
}

ASKDB_SETTINGS_ENCRYPTION_KEY="$(local_deploy_load_encryption_key)" || exit 1
ASKDB_DATABASE_DSN="$(local_deploy_load_database_dsn)" || exit 1
export ASKDB_SETTINGS_ENCRYPTION_KEY
export ASKDB_DATABASE_DSN
export ASKDB_AGENT_DATA_DIR="$LOCAL_DEPLOY_DATA_DIR"
export ASKDB_AGENT_MEMORY_CORPUS_DIR="$LOCAL_DEPLOY_DATA_DIR/agent-memory-corpus"
export ASKDB_WREN_DATA_DIR="$LOCAL_DEPLOY_AGENT_DIR/data/wren"
export WREN_HOME="$LOCAL_DEPLOY_AGENT_DIR/data/wren-home"
export ASKDB_MEMORY_JOURNAL_PATH="$LOCAL_DEPLOY_JOURNAL_PATH"

cd "$LOCAL_DEPLOY_AGENT_DIR"
exec "$AGENT_CLI" "$@"
