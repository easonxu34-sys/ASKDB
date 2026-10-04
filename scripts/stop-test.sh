#!/usr/bin/env bash
# Stop only this Compose project; keep its persistent named volume.
set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$REPO_ROOT/.env.docker"
fail() { printf '错误: %s\n' "$1" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || fail '未找到 Docker；请安装 Docker Desktop。'
docker compose version >/dev/null 2>&1 || fail 'Docker Compose 不可用。'
[[ -f "$ENV_FILE" ]] || fail '缺少 .env.docker；请恢复该项目的配置文件后停止。'
docker info >/dev/null 2>&1 || fail 'Docker daemon 不可用；请启动 Docker Desktop 后重试。'

docker compose --project-name askdb-local-test --project-directory "$REPO_ROOT" \
  --file "$REPO_ROOT/compose.yaml" --env-file "$ENV_FILE" down
printf 'askdb-local-test 已停止；agent_data 数据卷已保留。\n'
