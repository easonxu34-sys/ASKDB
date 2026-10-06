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

## 本机 Docker + Tailscale Funnel 公网演示

此方案在本机 Docker Desktop 中运行 Agent 与 Web，再由 Tailscale Funnel 提供稳定的 `*.ts.net` HTTPS 地址，无需购买域名或配置 Cloudflare。Mac 需要安装并登录 Tailscale，并确保命令行可调用 `tailscale`；Standalone 客户端可在设置中启用 CLI 集成。Tailscale 的 macOS 客户端版本对 Funnel 有限制，若命令提示当前版本不支持，再按官方 macOS 版本说明切换到支持 Funnel 的变体。首次启动时按提示启用 Funnel、MagicDNS 和 HTTPS。公网地址由本机的 Tailscale 名称生成，重启后保持不变；演示时 Mac、Docker 和 Tailscale 必须保持在线。个人免费方案受 Tailscale 使用条款限制，且 Funnel 有带宽限制。Agent 镜像固定为 `linux/amd64`，在 Apple Silicon 上由 Docker Desktop 模拟运行，以使用 Wren 的预编译 wheel；首次构建需要网络访问镜像仓库和依赖源。

### 首次配置

1. 安装并登录 Tailscale for macOS，并确保 `tailscale` CLI 可用。Standalone 客户端可在设置中安装 CLI 集成；macOS 客户端变体对 Funnel 的支持有限，若 Funnel 命令提示版本不支持，请按 [Tailscale macOS 版本说明](https://tailscale.com/docs/concepts/macos-variants) 切换到支持的变体。首次启动时会引导你在浏览器中批准 Funnel。
2. 在仓库根目录复制配置并限制文件权限：

   ```bash
   cp .env.docker.example .env.docker
   chmod 600 .env.docker
   ```

   编辑 `.env.docker`：填写 `ASKDB_SETTINGS_ENCRYPTION_KEY`。启动脚本会自动从 Tailscale Funnel 读取固定 HTTPS 地址并传给 Web；无需手动填写公网域名。模型在网页设置中配置，不需要在此文件放模型 API Key。生成 Fernet 密钥的现有命令如下；请在已安装 cryptography 的 Python 环境执行，也可在安装了 Python/uv 的 Agent 开发环境中通过 `uv run python` 执行：

   ```bash
   python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
   ```

   `.env.docker` 已被 Git 和 Docker 构建上下文排除；请自行保护本机文件及备份，不要将 token、模型密钥或 Fernet 密钥提交到仓库。不要设置 `WREN_PROJECT_DIR`，首次通过 UI 创建数据源。
3. 启动 Agent 和 Web：

   ```bash
   ./scripts/start-test.sh
   ```

   脚本从自身位置定位仓库，构建并启动 `askdb-local-test`，等待 Agent 与 Web 健康，并启用 Tailscale Funnel 的 HTTPS `:8443` 入口转发到本机 `3001`。首次启用时可能要求你在浏览器批准 Funnel。成功后会显示本机和公网地址；公网 hostname 与端口固定，重启后可继续使用。`--bg` 会让 Funnel 在后台保持运行，因此启动终端可以关闭；Mac、Docker 和 Tailscale 仍须在线。显示本地就绪不等于公网登录和 SSE 聊天已验证。可用以下命令查看此项目状态及日志；分享日志前检查是否包含敏感内容：

   ```bash
   docker compose --env-file .env.docker ps
   docker compose --env-file .env.docker logs --tail=100 agent web
   ```
4. 在本机交互式终端进入容器初始化首位管理员，保留默认 TTY，不添加 `-T`：

   ```bash
   docker compose --env-file .env.docker exec agent askdb-agent auth init-admin
   ```

   输入自选用户名；系统生成的临时密码只显示一次，没有默认账号或密码。此命令仅在没有账号时成功。通过安全渠道交付临时密码，在配置的 **HTTPS 地址**登录并立即更换密码。`http://localhost:3001` 用于 Docker 页面/健康检查；登录和其他受保护操作使用公网 HTTPS 地址。
5. 管理员在 UI 中配置模型、创建数据源、设置连接凭证并构建语义模型，再为普通用户创建账号、分配启用的数据源。真实问数需要可用模型凭证、容器可连接的数据库地址，以及数据库端授予只读权限的账号。数据库位于宿主机时，Docker Desktop 可使用 `host.docker.internal`；容器中的 `localhost` 指向容器自身。仅连接获得授权的测试数据。

### 公网边界与数据保留

公网 hostname 的访问者能到达登录页；本方案用于授权测试数据，请限制临时链接传播。它不会自动创建管理员，也不会自动验证真实 SQL 查询、数据库权限或公网登录。Agent API 仅在 Compose 内网 `http://agent:8000` 可达，未映射宿主机端口；只有 Web 发布到 `127.0.0.1:3001`，公网入口经 Tailscale Funnel 到 Web。不要额外公开 Agent 的 8000 端口。

停止服务及隧道：

```bash
./scripts/stop-test.sh
```

脚本仅对本项目执行 Compose `down`，关闭本项目使用的 Tailscale Funnel `:8443` 入口及遗留的 localhost.run SSH 隧道；保留 `agent_data` 命名卷（实际卷名通常为 `askdb-local-test_agent_data`）。该卷保存 SQLite 账号/会话、模型与数据源配置、Wren 文件和持久记忆；重复启动继续使用这些数据。仓库中的 `wren-project/` 只读挂载到 Agent 的 `/app/wren-template` 作为参考模板，不会被用作活动项目或写入。备份时同时保护数据卷与原 Fernet 密钥，不要在重启时重新生成密钥。

以下命令是**破坏性重置**，会删除本项目命名卷，丢失账号、配置和持久记忆，仅在确定不再需要这些测试数据时执行：

```bash
docker compose --env-file .env.docker down --volumes
```

容器内管理员恢复及安全说明见 [`askdb-agent/README.md`](askdb-agent/README.md#docker-测试部署)。

## 当前状态

UI 已切换为调用 Agent 的流式适配器；Agent 实现了 SSE API、只读 SQL 检查和 Wren 查询前的 dry-plan / dry-run 门禁。TypeScript、生产构建和 10 项 Python 测试通过。端到端真实数据查询还需配置 Wren 项目/MDL、MySQL 只读账号和模型服务。
