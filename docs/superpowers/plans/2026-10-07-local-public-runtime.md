# 本机本地公网部署实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将公网演示切换到宿主机本地打包与进程运行，并保留旧 Docker 数据和可回退副本。

**Architecture:** Agent 与 Web 源码复制到 `~/Library/Application Support/ASKDB-Agent/app` 并在宿主机打包；Agent 监听 loopback `8001`，Next.js 生产服务监听 loopback `3001`；Tailscale Funnel `:8443` 只转发 Web。旧 Compose 数据通过一次性迁移导入 Application Support，普通启停不依赖 Docker。

**Tech Stack:** macOS、Python 3.13、uv、FastAPI/Uvicorn、Node.js 22+、pnpm、Next.js、SQLite、Tailscale Funnel。

**Spec:** `docs/superpowers/specs/2026-10-07-local-public-runtime-design.md`

## Global Constraints

- Agent 绑定 `127.0.0.1:8001`；Web 绑定 `127.0.0.1:3001`；公网只到 Web 的 Funnel HTTPS `:8443`。
- Agent 按 `uv.lock` 安装；Web 按 `pnpm-lock.yaml` 安装；不构建 Docker 镜像。
- 运行源码、Python 环境、密钥和数据放在 `~/Library/Application Support/ASKDB-Agent`，普通停止不得删除数据或旧 `agent_data` 卷。
- 迁移使用 Docker 只导出现有卷；先停止旧 Web/Agent、校验 SQLite，再切换目录；迁移失败恢复原来运行的服务。
- 使用旧 Fernet key 解密迁移数据；密钥不传给 Web、不打印、不写入仓库。
- 通过当前用户 GUI launchd 域托管 Agent/Web；停止只卸载这两个固定服务标签。
- Web 的 `ASKDB_WEB_ORIGIN` 必须是 Funnel HTTPS origin；保留现有 CSRF 契约和 SSE 流量路径。
- 不运行测试套件；通过 shell 语法检查、锁定构建和运行健康检查验证。

---

### Task 1: 本地构建和进程生命周期脚本

**Files:**
- Create: `scripts/local-deploy-common.sh`
- Create: `scripts/askdb-agent-local.sh`
- Create: `scripts/run-local-agent.sh`
- Create: `scripts/run-local-web.sh`
- Create: `scripts/write-local-launchd-plists.py`
- Modify: `scripts/start-test.sh`
- Modify: `scripts/stop-test.sh`
- Modify: `.gitignore`
- Create: `.env.local.example`

**Interfaces:**
- `local_deploy_load_encryption_key` 只输出 Fernet key 给调用方 shell，不向终端或 Web 输出。
- `start-test.sh` 同步源码到 Application Support、创建本机 Python/Web production build 和 launchd 配置，Agent `8001` 与 Web `3001` 通过健康检查后报告 Funnel URL。
- `stop-test.sh` 只卸载两个固定 launchd 标签，关闭 Funnel 并保留 Application Support 数据目录。
- `askdb-agent-local.sh <args...>` 在同一数据目录和 Fernet key 下运行 Agent 管理 CLI。

- [x] **Step 1: 添加共享路径、密钥读取和 launchd 管理 helper**
- [x] **Step 2: 将公网启动改为 uv 与 pnpm 锁定安装、Next Webpack 生产构建并注册 launchd 服务**
- [x] **Step 3: 将停止脚本改为卸载指定服务、保留数据并关闭 Funnel**
- [x] **Step 4: 添加 Agent/Web launchd 入口、CLI wrapper 和 Git 忽略规则**
- [x] **Step 5: 对 Shell 脚本做语法检查并审查进程/密钥边界**

### Task 2: 安全迁移 Docker 数据卷

**Files:**
- Create: `scripts/migrate-docker-data-local.sh`
- Modify: `docs/superpowers/specs/2026-10-07-local-public-runtime-design.md`

**Interfaces:**
- Migration reads the exact `askdb-local-test` Compose project and old `.env.docker`; it does not build images or delete the named volume.
- 迁移成功后会在 `~/Library/Application Support/ASKDB-Agent/data/.migrated-from-docker-volume` 写入来源标记，旧容器保持停止，原卷保留用于回退。

- [x] **Step 1: Validate old project, destination safety, and running state**
- [x] **Step 2: Stop old Agent/Web and copy `/app/data` into a private staging directory**
- [x] **Step 3: Run SQLite quick check and atomically install the copy**
- [x] **Step 4: 迁移现有旧卷数据并确认数据完整、旧 Docker 卷保留**

### Task 3: Update operator documentation

**Files:**
- Modify: `README.md`
- Modify: `askdb-agent/README.md`
- Rename/update: `docs/superpowers/specs/2026-10-07-local-public-runtime-design.md`

- [x] **Step 1: Document prerequisites, local database addressing, Tailscale HTTPS origin, and local start/stop commands**
- [x] **Step 2: Document one-time migration, Fernet key continuity, CLI admin init/recovery, backup, and rollback boundaries**
- [x] **Step 3: 复核文档，并区分本机构建/健康检查与公网/SSE 端到端验证边界**
