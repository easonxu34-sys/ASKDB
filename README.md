# AskDB Agent

AskDB 是一个自然语言问数产品，当前工作区按职责拆成两个应用：

- [`askdb-web/`](askdb-web/)：基于 assistant-ui 的聊天界面和 Next.js SSE 代理。
- [`askdb-agent/`](askdb-agent/)：FastAPI + LangGraph + WrenToolkit 服务。
- [`docs/开发文档.md`](docs/开发文档.md)：架构、开发顺序、安全边界和验收条件。

## 产品演示

[▶️ 点击查看 AskDB 演示视频（MP4）](docs/assets/askdb-demo.mp4)

## 架构概览

浏览器中的聊天页面通过 Next.js 同源 BFF 将请求转发给 FastAPI Agent。Agent 使用 LangGraph 编排对话和工具调用，再通过 WrenToolkit 调用 Wren 的语义模型、SQL 规划与执行能力，访问授权范围内的只读数据源。回答和查询结果通过 SSE 流式返回 Web。

```text
用户 → Next.js Web / BFF → FastAPI → LangGraph Agent → WrenToolkit / Wren → 只读数据源
                         ←────────────── SSE 流式响应 ──────────────
```

## 功能操作

1. 管理员登录后配置模型和数据源，并为用户分配可访问的数据源；数据源使用只读账号。
2. 用户登录，选择有权限的数据源，在聊天框用自然语言提出分析问题。
3. Agent 基于 Wren 语义模型执行受控查询，并流式返回回答、结果表格和可用图表。
4. 用户可以编辑图表并先预览再应用，也可以选择结果数据填入追问后手动发送。
5. 按需导出图表 PNG 或查询结果 CSV。

当前已包含部署级全局模型设置入口与 Agent 设置 API；Agent 使用 PostgreSQL 保存账号、模型配置、数据源、会话和记忆元数据，Fernet 密钥、可信内网访问边界和 HTTPS 仍需按部署配置。

## 本地开发

Web 端：

```bash
npm --prefix askdb-web install
npm run dev
```

根目录的 `npm run dev` 会转发到 `askdb-web`。首次使用先安装 Web 依赖。

Agent 服务需要 Python 3.11+ 和 uv。真实问数需在管理页面配置模型、只读数据源和语义模型（MDL）；仓库不附带特定数据库的 Wren 项目。具体步骤见 [`askdb-agent/README.md`](askdb-agent/README.md)。

## 本机本地构建 + Tailscale Funnel 公网演示

公网演示在 Mac 上把 Agent/Web 源码复制到用户级 Application Support 后本机打包和运行，不构建或运行 Docker 镜像。Tailscale Funnel 提供稳定的 `*.ts.net` HTTPS 地址，公网只进入 Web；Agent 绑定 `127.0.0.1:8001`，Web 绑定 `127.0.0.1:3001`。Mac 必须开机、保持当前用户登录并运行 Tailscale。Funnel 支持当前聊天所需的 SSE；个人免费方案仍受 Tailscale 使用条款和带宽限制。

源码相同不代表容器和本机运行环境完全相同：Dockerfile 使用 Linux 容器，而 Mac 使用本机操作系统和 CPU 架构，带有本机代码的依赖会采用不同 wheel/动态库。这里直接使用本机验证过的 Wren 运行环境，并按 `uv.lock`、`pnpm-lock.yaml` 安装锁定依赖。启动需要 Python 3.13、uv、Node.js 22+、pnpm、Tailscale CLI；部分数据库驱动还需要本机的编译工具和数据库客户端库。

### 首次配置

1. 安装并登录 Tailscale for macOS，确保 `tailscale` CLI 可用。Standalone 客户端可在设置中安装 CLI 集成；macOS 客户端变体若不支持 Funnel，按 [Tailscale macOS 版本说明](https://tailscale.com/docs/concepts/macos-variants)切换到支持的变体。首次启用时按提示批准 Funnel、MagicDNS 和 HTTPS。
2. 新安装时创建本机密钥文件：

   ```bash
   cp .env.local.example .env.local
   chmod 600 .env.local
   ```

   在 `.env.local` 设置 `ASKDB_SETTINGS_ENCRYPTION_KEY`。生成 Fernet 密钥可执行：

   ```bash
   uv run --project askdb-agent python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   ```

   将 `askdb-agent/.env.example` 复制为 `askdb-agent/.env`，在其中设置 PostgreSQL DSN。默认值 `service=askdb-agent-dev` 对应 `~/.pg_service.conf` 中的同名 service；也可填 PostgreSQL URL。启动脚本从 Agent 模块的 `.env` 读取 DSN，并将它和 Fernet key 写入 `~/Library/Application Support/ASKDB-Agent/secrets.env`（权限 `0600`）。这两个 env 文件均会被 Git 忽略。备份 PostgreSQL 数据库时必须同时保护 Fernet key。模型凭证仍在登录后的设置页面配置。
3. 构建并启动公网版：

   ```bash
   ./scripts/start-test.sh
   ```

   脚本把 Agent/Web 源码复制到 `~/Library/Application Support/ASKDB-Agent/app/`，在那里按锁文件构建；Python 环境、运行数据、密钥、launchd 配置和日志也放在同一私有目录。它随后注册当前用户的两个 launchd 后台服务，等待 Agent `/healthz` 和 Web 首页就绪，再把 Funnel HTTPS `:8443` 指向本机 `3001`，从 Tailscale 读取公网 origin 并写入 Web 服务配置。首次启动可能需要浏览器批准 Funnel。服务会在启动终端关闭后继续运行；用户注销或 Mac 休眠/关机时公网服务不可用。成功后会显示本机健康地址和公网地址；公网登录及受保护操作请使用 HTTPS 公网地址，本机地址仅用于健康检查。
4. 在有交互 TTY 的终端创建首位管理员：

   ```bash
   scripts/askdb-agent-local.sh auth init-admin
   ```

   管理员临时密码只显示一次，没有默认账号密码。随后在 Web 设置页配置模型和数据源，使用只读数据库账号并构建语义模型。

### 本机数据库、运行状态和数据保留

Agent 在宿主机运行，因此数据库就在这台 Mac 上时，连接地址可用 `127.0.0.1` 或 `localhost`；若数据库运行在另一容器中，则使用映射到宿主机的端口或可达的主机名。Agent 仍只绑定 loopback，不能通过公网直接访问。

启动日志保存在 `~/Library/Application Support/ASKDB-Agent/logs/`；launchd 配置保存在该目录的 `run/`，源码副本保存在 `app/`。这些目录权限受限；分享日志前先检查是否包含敏感内容。健康检查地址为 `http://127.0.0.1:8001/healthz` 和 `http://127.0.0.1:3001/`。停止服务与 Funnel：

```bash
./scripts/stop-test.sh
```

停止脚本只卸载本部署使用的两个 launchd 标签，并关闭 Funnel `:8443`；不会按端口杀进程，也不会删除 `~/Library/Application Support/ASKDB-Agent/data`。PostgreSQL 保存账号、会话、模型与数据源配置及记忆元数据；本机数据目录只保存 Wren 文件、删除 journal 和记忆语料。分别备份 PostgreSQL、本机数据目录和 Fernet key。

公开主机名的访问者能到达登录页，因此只使用获得授权的测试数据。状态显示健康不代表公网登录、SSE 聊天、SQL 权限或真实查询已经完成端到端验证。

仓库中的 Compose 文件和 Dockerfile 保留给可选的容器开发；`start-test.sh` 和 `stop-test.sh` 的日常公网流程不依赖 Docker。

管理员恢复及本地运行说明见 [`askdb-agent/README.md`](askdb-agent/README.md#本机本地公网部署)。

## 当前状态

UI 已切换为调用 Agent 的流式适配器；Agent 实现了 SSE API、只读 SQL 检查和 Wren 查询前的 dry-plan / dry-run 门禁。TypeScript、生产构建和 10 项 Python 测试通过。端到端真实数据查询还需配置 Wren 项目/MDL、MySQL 只读账号和模型服务。
