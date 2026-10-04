# 本地 Docker 公网隧道部署实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在本地用 Docker Compose 运行 AskDB Agent 和生产 Web，并通过 Cloudflare 命名隧道公开 Web，保证聊天 SSE 可达浏览器。

**Architecture:** Agent、Next.js Web 和 cloudflared 作为三个容器运行在同一 Compose 网络。公网隧道只连接 Web；Agent 的 SQLite、Wren 配置和运行时数据保存在命名卷，Agent 不发布宿主机端口。

**Tech Stack:** Docker Compose、Python 3.13、uv、FastAPI/Uvicorn、Node.js 22、pnpm、Next.js、cloudflared 命名隧道。

**Spec:** `docs/superpowers/specs/2026-10-04-local-docker-public-tunnel-design.md`

## Global Constraints

- Agent 仅在容器网络监听 `8000`；不发布 Agent API 到宿主机或公网。
- Web 以 production server 运行在容器端口 `3000`，Agent 上游为 `http://agent:8000`。
- 只将 Web 端口绑定到宿主机 `127.0.0.1`；Cloudflare 命名隧道的发布路由指向 Web 服务名。
- 使用 Cloudflare 命名隧道与用户域名；Quick Tunnel 不支持 SSE，不用于公网聊天。
- Agent 的 `/app/data` 必须持久化；普通停止不得删除命名卷。`wren-project/` 只读挂载到 `/app/wren-template` 作为参考模板。
- Agent Dockerfile 的 uv helper image 必须固定为 `ghcr.io/astral-sh/uv:0.11.29`。
- 管理员初始化必须使用现有交互式 `askdb-agent auth init-admin`，不得引入默认账号或密码。
- 环境模板不得包含真实密钥；用户本地 `.env.docker` 必须被 Git 忽略。
- 本机 Docker daemon 当前未运行；需要在 daemon 启动后执行 Compose 构建和容器联通验证。

---

### Task 1: 建立 Agent 与 Web 镜像

**Files:**
- Create: `askdb-agent/Dockerfile`
- Create: `askdb-web/Dockerfile`

**Interfaces:**
- Agent image exposes container port `8000`, runs `uvicorn main:app`, and honors Compose-provided runtime environment variables.
- Web image exposes container port `3000`, runs the existing Next.js production start command, and reads `ASKDB_AGENT_URL` at server runtime.

- [x] **Step 1: Add the Agent Dockerfile**

Use a Python 3.13 slim base and copy `/uv` and `/uvx` from `ghcr.io/astral-sh/uv:0.11.29`. Copy only the Agent project metadata and `src/`, run `uv sync --locked --no-dev`, set the working directory to `/app/askdb-agent`, create `/app/data`, and start `uvicorn main:app --host 0.0.0.0 --port 8000`. Keep runtime data outside the image layer.

- [x] **Step 2: Add the Web Dockerfile**

Use Node.js 22 slim and `askdb-web/pnpm-lock.yaml`. Install with Corepack/pnpm in frozen-lockfile mode, run the existing production build, and start Next.js on `0.0.0.0:3000`. Do not add a new runtime dependency or expose build-time secrets.

- [x] **Step 3: Review image inputs**

Check the Docker build context excludes `.git`, local `.env` files, Python virtualenvs, `node_modules`, `.next`, and local Agent data. Preserve tracked source and lockfiles needed by both image builds.

---

### Task 2: Define Compose services, environment, and persistent storage

**Files:**
- Create: `compose.yaml`
- Create: `.dockerignore`
- Create: `.env.docker.example`
- Modify: `.gitignore`
- Modify: `askdb-web/lib/agent-proxy.ts`
- Create: `askdb-web/tests/agent-proxy.test.mjs`

**Interfaces:**
- `agent` serves `/healthz` on internal port `8000`, mounts named volume `agent_data` at `/app/data`, and mounts `./wren-project` read-only at `/app/wren-template` as a reference template.
- `web` points `ASKDB_AGENT_URL` to `http://agent:8000`, sets `ASKDB_AGENT_INTERNAL_HTTP_HOSTS=agent`, publishes only `127.0.0.1:3000:3000`, and waits for Agent health.
- `tunnel` runs cloudflared with the user's named tunnel token; the Cloudflare published route targets `http://web:3000`. No service opens an inbound host port except the local Web inspection port.

- [x] **Step 1: Add a fail-closed internal HTTP host allowlist**

Keep current loopback HTTP behavior. Parse `ASKDB_AGENT_INTERNAL_HTTP_HOSTS` as a comma-separated set of exact, case-insensitive hostnames; allow non-loopback HTTP only when the parsed set contains the exact hostname. Empty/unset configuration still rejects every non-loopback HTTP URL. Credentials and unsupported schemes remain rejected. Add Node tests for default rejection, explicit exact-host acceptance, rejection of a hostname suffix/lookalike, loopback compatibility, and URL credential rejection. Run only `node --test tests/agent-proxy.test.mjs` from `askdb-web/` for this focused behavior.

- [x] **Step 2: Define locked-in runtime paths**

Set `ASKDB_SETTINGS_DB_PATH=/app/data/model-settings.sqlite3`, `ASKDB_WREN_DATA_DIR=/app/data/wren`, and `WREN_HOME=/app/data/wren-home` in the Agent container. Mount `wren-project/` at `/app/wren-template` read-only as a reference, but do not set `WREN_PROJECT_DIR` by default: a non-empty value triggers legacy-project migration and fails when the ignored `target/mdl.json` artifact is absent. New data sources use the existing Web onboarding flow and persistent Wren data directory.

- [x] **Step 3: Add Compose health checks and service dependencies**

Probe Agent `/healthz` with Python's standard library and Web `/` with Node's built-in `fetch`. Start Web after Agent is healthy and tunnel after Web is healthy. Do not add an Agent `ports` mapping.

- [x] **Step 4: Add environment template and ignore rule**

Document `OPENAI_API_KEY`, `OPENAI_BASE_URL`, `ASKDB_MODEL`, `ASKDB_SETTINGS_ENCRYPTION_KEY`, `CLOUDFLARE_TUNNEL_TOKEN`, and `CLOUDFLARE_TUNNEL_HOSTNAME` in `.env.docker.example`; leave secret values blank. Ignore `.env.docker` and add an explicit ignore exception for the checked-in example.

---

### Task 3: Add lifecycle scripts and deployment instructions

**Files:**
- Create: `scripts/start-test.sh`
- Create: `scripts/stop-test.sh`
- Modify: `README.md`
- Modify: `askdb-agent/README.md`

**Interfaces:**
- `scripts/start-test.sh` runs Compose build/start, waits up to two minutes for readiness, and prints local and configured public URLs.
- `scripts/stop-test.sh` runs Compose down without `--volumes`; it does not use process-name or port-based killing.

- [x] **Step 1: Add safe startup and shutdown scripts**

Resolve repository root from the script location. Startup should fail with a clear message if Docker is unavailable or required model/tunnel values are absent, copy no secrets, launch only this Compose project, and print the configured `https://` hostname after local services become ready. Shutdown should stop only services in this project's Compose file and preserve `agent_data`.

- [x] **Step 2: Document first-run setup**

Explain how to create a named tunnel and publish its route to `http://web:3000` in Cloudflare, copy `.env.docker.example` to `.env.docker`, generate a Fernet key with the existing Python cryptography command, fill in the model key, tunnel token and hostname, start the stack, initialize the first admin interactively inside the Agent container, and configure model/data sources in the UI. State that the Agent API is internal only.

- [x] **Step 3: Document limitations and persistence**

Explain that stop retains the Docker volume; `docker compose down --volumes` is a destructive reset. Explain that public hostname visitors can reach the login page, so this is for authorized test data only. Note that real querying requires configured model credentials and a database account with read-only privileges.

- [ ] **Step 4: Validate the deployment artifacts**

Run `docker compose --env-file .env.docker config` after creating a local non-secret env file. This static config validation passed. Once Docker Desktop is running, run `scripts/start-test.sh`, inspect health/logs and confirm only Web is host-published. Image, container, and tunnel verification remains pending because the Docker daemon is unavailable.
