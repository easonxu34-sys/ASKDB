#!/usr/bin/env bash
# One-time, quiesced copy of the old Compose agent_data volume to the host.
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/local-deploy-common.sh"

ENV_FILE="$LOCAL_DEPLOY_REPO_ROOT/.env.docker"
COMPOSE=(docker compose --project-name askdb-local-test --project-directory "$LOCAL_DEPLOY_REPO_ROOT" --file "$LOCAL_DEPLOY_REPO_ROOT/compose.yaml" --env-file "$ENV_FILE")
STAGING_DIR=""
AGENT_WAS_RUNNING=false
WEB_WAS_RUNNING=false
MIGRATION_FINISHED=false
IMPORTED_DATA_DIR=""

fail() {
  local_deploy_fail "$1"
  exit 1
}

restore_compose_on_failure() {
  local exit_status=$?
  if [[ "$MIGRATION_FINISHED" != true && $exit_status -ne 0 ]]; then
    if [[ -n "$STAGING_DIR" ]]; then
      rm -rf -- "$STAGING_DIR"
    fi
    if [[ -n "$IMPORTED_DATA_DIR" ]]; then
      rm -rf -- "$IMPORTED_DATA_DIR"
    fi
    if [[ "$AGENT_WAS_RUNNING" == true ]]; then
      "${COMPOSE[@]}" start agent >/dev/null 2>&1 || true
    fi
    if [[ "$WEB_WAS_RUNNING" == true ]]; then
      "${COMPOSE[@]}" start web >/dev/null 2>&1 || true
    fi
  fi
}
trap restore_compose_on_failure EXIT

command -v docker >/dev/null 2>&1 || fail '未找到 Docker CLI；迁移只使用旧容器导出数据，不会构建镜像。'
docker info >/dev/null 2>&1 || fail 'Docker daemon 不可用；请启动当前旧部署后重试迁移。'
[[ -f "$ENV_FILE" ]] || fail '缺少旧部署的 .env.docker，无法准确读取原 Compose 项目。'
"${COMPOSE[@]}" config --quiet || fail '旧 compose.yaml 或 .env.docker 无效。'

mkdir -p -m 700 "$LOCAL_DEPLOY_DIR" "$LOCAL_DEPLOY_RUN_DIR" "$LOCAL_DEPLOY_LOG_DIR"
chmod 700 "$LOCAL_DEPLOY_DIR" "$LOCAL_DEPLOY_RUN_DIR" "$LOCAL_DEPLOY_LOG_DIR"
if [[ -d "$LOCAL_DEPLOY_DATA_DIR" ]]; then
  if find "$LOCAL_DEPLOY_DATA_DIR" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
    fail 'Application Support 中的活动数据目录已有内容；为避免覆盖本地数据，迁移已停止。'
  fi
  rmdir "$LOCAL_DEPLOY_DATA_DIR"
fi

AGENT_CONTAINER="$("${COMPOSE[@]}" ps --all --quiet agent)"
[[ -n "$AGENT_CONTAINER" && "$AGENT_CONTAINER" != *$'\n'* ]] || fail '未找到唯一的 askdb-local-test Agent 容器。'
WEB_CONTAINER="$("${COMPOSE[@]}" ps --all --quiet web)"
[[ -n "$WEB_CONTAINER" && "$WEB_CONTAINER" != *$'\n'* ]] || fail '未找到唯一的 askdb-local-test Web 容器。'
[[ "$(docker inspect --format '{{.State.Running}}' "$AGENT_CONTAINER")" == true ]] && AGENT_WAS_RUNNING=true
[[ "$(docker inspect --format '{{.State.Running}}' "$WEB_CONTAINER")" == true ]] && WEB_WAS_RUNNING=true

STAGING_DIR="$(mktemp -d "$LOCAL_DEPLOY_DIR/.data-import.XXXXXX")"
printf '停止旧 Compose Agent/Web，确保 SQLite 和 Wren 文件静止…\n'
"${COMPOSE[@]}" stop web agent

printf '从旧 agent_data 卷复制到本机私有目录…\n'
docker cp "$AGENT_CONTAINER:/app/data/." "$STAGING_DIR/"
if ! find "$STAGING_DIR" -mindepth 1 -maxdepth 1 -print -quit | grep -q .; then
  fail '旧 Agent 数据目录为空；没有安装新运行时，请检查旧卷。'
fi
if [[ -f "$STAGING_DIR/model-settings.sqlite3" ]]; then
  DB_FILE="$STAGING_DIR/model-settings.sqlite3" python3 - <<'PY'
import os
import sqlite3

connection = sqlite3.connect(os.environ["DB_FILE"])
try:
    result = connection.execute("PRAGMA quick_check").fetchone()
    if not result or result[0] != "ok":
        raise SystemExit(1)
finally:
    connection.close()
PY
else
  fail '旧卷缺少 model-settings.sqlite3；拒绝将不完整数据目录设为活动目录。'
fi

chmod -R go-rwx "$STAGING_DIR"
printf 'source=askdb-local-test_agent_data\n' > "$STAGING_DIR/.migrated-from-docker-volume"
chmod 600 "$STAGING_DIR/.migrated-from-docker-volume"
mv "$STAGING_DIR" "$LOCAL_DEPLOY_DATA_DIR"
STAGING_DIR=""
IMPORTED_DATA_DIR="$LOCAL_DEPLOY_DATA_DIR"
python3 "$SCRIPT_DIR/relocate-docker-data-paths.py" \
  "$LOCAL_DEPLOY_DATA_DIR/model-settings.sqlite3" "$LOCAL_DEPLOY_DATA_DIR" || fail '切换 SQLite 内 Wren 项目路径失败；旧 Docker 数据卷仍保留。'
chmod 700 "$LOCAL_DEPLOY_DATA_DIR"
chmod 600 "$LOCAL_DEPLOY_DATA_DIR/model-settings.sqlite3"
MIGRATION_FINISHED=true
IMPORTED_DATA_DIR=""
printf '数据已复制到 %s/data；原 Docker 数据卷和已停止容器仍保留，可用于回退。\n' "$LOCAL_DEPLOY_DIR"
printf '下一步运行 scripts/start-test.sh 构建并启动本机公网版本。\n'
