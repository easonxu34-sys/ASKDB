# AskDB Agent

AskDB 是一个自然语言问数产品，当前工作区按职责拆成两个应用：

- [`askdb-web/`](askdb-web/)：基于 assistant-ui 的聊天界面和 Next.js SSE 代理。
- [`askdb-agent/`](askdb-agent/)：FastAPI + LangGraph + WrenToolkit 服务。
- [`docs/开发文档.md`](docs/开发文档.md)：架构、开发顺序、安全边界和验收条件。

当前已包含部署级全局模型设置入口与 Agent 设置 API；生产部署前需配置持久化 SQLite 路径、Fernet 加密密钥、可信内网访问边界和 HTTPS。

## 本地开发

Web 端：

```bash
npm --prefix askdb-web install
npm run dev
```

根目录的 `npm run dev` 会转发到 `askdb-web`。首次使用先安装 Web 依赖。

Agent 服务需要 Python 3.11+、uv、一个已构建 MDL 的 Wren 项目和只读数据库 profile。具体步骤见 [`askdb-agent/README.md`](askdb-agent/README.md)。

## 本机 Docker + Cloudflare 公网测试

此方案在本机运行 Agent、Web 和 cloudflared，通过 Cloudflare **命名隧道**提供固定 HTTPS 测试地址。需要已启动的 Docker Desktop、支持 `up --wait` 的 Docker Compose，以及已接入 Cloudflare DNS 的域名和创建隧道的权限。无需在本机安装 Node、uv 或 Wren；首次构建需要网络访问镜像仓库和依赖源。

### 首次配置

1. 在 Cloudflare Zero Trust 的 Networks / Tunnels 中创建命名隧道，选择 cloudflared connector，取得该隧道的 token。只保存 token，不执行控制台提供的宿主机安装命令；本项目会在容器中运行 connector。
2. 为隧道添加 Published application route（公开主机名），例如 `askdb.example.com`，将服务设为 **HTTP**、地址 **`web:3000`**，即 `http://web:3000`。不要填写 localhost，因为 connector 与 Web 是不同容器。确认该主机名的 DNS 记录指向此隧道；控制台通常会自动创建记录，若有冲突则先处理冲突，并确认 hostname 属于当前 Cloudflare zone。
3. 在仓库根目录复制配置并限制文件权限：

   ```bash
   cp .env.docker.example .env.docker
   chmod 600 .env.docker
   ```

   编辑 `.env.docker`：填写 `ASKDB_SETTINGS_ENCRYPTION_KEY`、`CLOUDFLARE_TUNNEL_TOKEN` 和 `CLOUDFLARE_TUNNEL_HOSTNAME`。模型不需要写入环境文件；管理员初始化后从 Web 的“模型设置”页面新增模型和 API Key。
   hostname 只填写域名，不包含 `https://`、端口或路径。生成 Fernet 密钥的现有命令如下；请在已安装 cryptography 的 Python 环境执行，也可在安装了 Python/uv 的 Agent 开发环境中通过 `uv run python` 执行：

   ```bash
   python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   ```

   `.env.docker` 已被 Git 和 Docker 构建上下文排除；请自行保护本机文件及备份，不要将 token、模型密钥或 Fernet 密钥提交到仓库。不要设置 `WREN_PROJECT_DIR`，首次通过 UI 创建数据源。
4. 启动：

   ```bash
   ./scripts/start-test.sh
   ```

   脚本从自身位置定位仓库，构建并启动 `askdb-local-test`，构建后最多等待 120 秒就绪。缺少 Docker、必填配置或 daemon 未启动时会明确失败。首次构建时间不计入就绪等待。成功后显示本机与配置的 HTTPS 地址；显示 HTTPS 地址仅表示配置值，本地就绪不等于公网隧道、DNS 或登录已验证。可用以下命令查看此项目状态及日志；对外分享日志前检查是否包含敏感内容：

   ```bash
   docker compose --env-file .env.docker ps
   docker compose --env-file .env.docker logs --tail=100 agent web tunnel
   ```
5. 在本机交互式终端进入容器初始化首位管理员，保留默认 TTY，不添加 `-T`：

   ```bash
   docker compose --env-file .env.docker exec agent askdb-agent auth init-admin
   ```

   输入自选用户名；系统生成的临时密码只显示一次，没有默认用户名或默认密码。此命令仅在没有账号时成功。通过内部渠道交付临时密码，在配置的 **HTTPS 地址**登录并立即更换密码。`http://localhost:3000` 用于本机页面/健康检查；当前 Web 的 `ASKDB_WEB_ORIGIN` 为公网 HTTPS，登录和其他受保护操作请使用该 HTTPS 地址。
6. 管理员先在 UI 的“模型设置”页面新增并测试一个模型，再创建数据源、设置连接凭证并构建语义模型，最后为普通用户创建账号、分配启用的数据源。模型 API Key 会加密保存在 Agent 的持久化 SQLite 配置中。真实问数需要可用模型凭证、容器可连接的数据库地址，以及数据库端授予只读权限的账号。数据库位于宿主机时，Docker Desktop 可使用 `host.docker.internal`；容器中的 `localhost` 指向容器自身。仅连接获得授权的测试数据。

### 公网边界与数据保留

公网 hostname 的访问者能到达登录页；本方案用于授权测试数据，请限制链接传播并按需要在 Cloudflare 配置额外访问控制。它不会自动创建管理员，也不会自动验证真实 SQL 查询、数据库权限或公网登录。Agent API 仅在 Compose 内网 `http://agent:8000` 可达，未映射宿主机端口；只有 Web 发布到 `127.0.0.1:3000`，公网入口经隧道到 Web。不要额外公开 Agent 的 8000 端口。

停止服务及隧道：

```bash
./scripts/stop-test.sh
```

脚本仅对本项目执行 Compose `down`，保留 `agent_data` 命名卷（实际卷名通常为 `askdb-local-test_agent_data`）。该卷保存 SQLite 账号/会话、模型与数据源配置、Wren 文件和持久记忆；重复启动继续使用这些数据。仓库中的 `wren-project/` 只读挂载到 Agent 的 `/app/wren-template` 作为参考模板，不会被用作活动项目或写入。备份时同时保护数据卷与原 Fernet 密钥，不要在重启时重新生成密钥。

以下命令是**破坏性重置**，会删除本项目命名卷，丢失账号、配置和持久记忆，仅在确定不再需要这些测试数据时执行：

```bash
docker compose --env-file .env.docker down --volumes
```

容器内管理员恢复及安全说明见 [`askdb-agent/README.md`](askdb-agent/README.md#docker-测试部署)。

## 当前状态

UI 已切换为调用 Agent 的流式适配器；Agent 实现了 SSE API、只读 SQL 检查和 Wren 查询前的 dry-plan / dry-run 门禁。TypeScript、生产构建和 10 项 Python 测试通过。端到端真实数据查询还需配置 Wren 项目/MDL、MySQL 只读账号和模型服务。
