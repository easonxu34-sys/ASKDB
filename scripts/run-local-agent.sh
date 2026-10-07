#!/usr/bin/env bash
# launchd entry point for the host-local Agent.
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local-deploy-common.sh"

ASKDB_SETTINGS_ENCRYPTION_KEY="$(local_deploy_load_encryption_key)"
ASKDB_DATABASE_DSN="$(local_deploy_load_database_dsn)"
export ASKDB_SETTINGS_ENCRYPTION_KEY
export ASKDB_DATABASE_DSN
export ASKDB_AGENT_DATA_DIR="$LOCAL_DEPLOY_DATA_DIR"
export ASKDB_WREN_DATA_DIR="$LOCAL_DEPLOY_DATA_DIR/wren"
export WREN_HOME="$LOCAL_DEPLOY_DATA_DIR/wren-home"

mkdir -p -m 700 "$LOCAL_DEPLOY_DATA_DIR" "$LOCAL_DEPLOY_DATA_DIR/wren" "$LOCAL_DEPLOY_DATA_DIR/wren-home"
cd "$LOCAL_DEPLOY_AGENT_DIR"
exec "$LOCAL_DEPLOY_VENV_DIR/bin/uvicorn" main:app --host 127.0.0.1 --port "$LOCAL_DEPLOY_AGENT_PORT"
