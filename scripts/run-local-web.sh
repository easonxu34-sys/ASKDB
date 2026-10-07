#!/usr/bin/env bash
# launchd entry point for the host-local Next.js production server.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local-deploy-common.sh"

NODE_BIN="${1:?missing absolute Node.js executable path}"
cd "$LOCAL_DEPLOY_WEB_DIR"
exec "$NODE_BIN" "$LOCAL_DEPLOY_WEB_DIR/node_modules/next/dist/bin/next" start \
  --hostname 127.0.0.1 --port "$LOCAL_DEPLOY_WEB_PORT"
