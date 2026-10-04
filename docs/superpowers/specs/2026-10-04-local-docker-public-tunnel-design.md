# ASKDB-Agent 本地 Docker 公网测试部署设计

## 状态

待用户审阅。参考 `Ask-DB/start-test.sh` 和 `stop-test.sh` 中 Cloudflare Quick Tunnel 的用法，但不复制其宿主机进程管理方式或固定登录凭据。

## 目标

在当前 ASKDB-Agent 仓库提供一套可重复的本地 Docker Compose 部署：容器运行 Agent 与生产模式 Web，由 Cloudflare Quick Tunnel 把 Web 临时映射到公网。

## 方案

Compose 包含三个服务：

- `agent`：基于 `askdb-agent/` 构建，监听容器内 `8000`。不发布宿主机端口。
- `web`：基于 `askdb-web/` 构建，运行 Next.js production server，监听容器内 `3000`，并通过 `ASKDB_AGENT_URL=http://agent:8000` 调用 Agent。可选地只绑定 `127.0.0.1:3000` 供本机检查。
- `tunnel`：使用 cloudflared 容器执行 Quick Tunnel，目标为 `http://web:3000`。只暴露临时 Cloudflare URL，不需要隧道账户或固定域名；停止后该 URL 失效，重启会产生新 URL。

浏览器请求始终进入 Web 的同源 API 路由。Agent API、Wren CLI、SQLite 和数据源凭证都留在 Compose 网络/本地持久卷，不直接映射到公网。

## 持久化与密钥

- Agent 的 `/app/data` 使用 Compose 命名卷，保存 SQLite 账号/模型设置、加密记忆、Wren 配置和 Wren 数据源修订。
- 仓库中的 `wren-project/` 以只读方式提供给 Agent 作为现有语义项目模板；运行时生成的数据留在命名卷。
- 提供不含真实凭据的 Docker 环境模板，要求操作者本地复制并填写模型配置和 Fernet 加密密钥；实际 `.env` 不提交 Git。
- 不设置通用管理员账号或固定密码。首次启动后通过 `docker compose exec -it agent askdb-agent auth init-admin` 交互式生成管理员临时密码。
- `stop-test.sh` 仅停止本 Compose 项目并保留持久卷；删除卷的重置操作不属于普通停止流程。

## 仓库交付物

- 根目录 Compose 文件，定义内部服务网络、环境变量、健康检查和数据卷。
- Agent 与 Web 各自的 Dockerfile，使用锁文件构建依赖并以生产命令启动。
- Docker 环境变量示例及 `.gitignore` 例外规则。
- 参考 `start-test.sh` / `stop-test.sh` 的启动与停止脚本：启动时等待服务就绪并从 tunnel 日志提取 URL；停止时只操作本 Compose 项目。
- 根 README 与 Agent README 中的 Docker 首次启动、管理员初始化、Wren 数据源准备、停止和数据保留说明。

## 操作流程

1. 安装并启动 Docker Desktop，准备 Docker 环境文件。
2. 执行启动脚本构建并启动 Compose；脚本输出本机检查地址与临时公网地址。
3. 在 Agent 容器交互终端初始化管理员，再从公网 Web 页面登录并按要求修改临时密码。
4. 按产品现有流程配置模型和数据源。数据库连接仍使用数据库层只读账号。
5. 执行停止脚本关闭 Web、Agent 和隧道，保留 Agent 数据卷。

## 验收

- Compose 配置可解析，Agent 与 Web 镜像可构建。
- Web 容器能通过服务名访问 Agent；Agent 不存在宿主机或公网发布端口。
- 启动脚本在容器就绪后输出有效的 `trycloudflare.com` 地址，停止脚本仅停止当前 Compose 服务且不删除卷。
- 管理员初始化仍然要求交互终端，脚本和模板不包含默认账号密码或 API Key。
- 重启容器后 Agent SQLite/Wren 数据仍保留。
- 公网访问只到 Web，登录使用 HTTPS Cookie；公开链接持有者可访问登录页面，因此不得填入生产凭据或用未经授权的数据源。

## 边界与前提

- 本方案是本地测试部署。Quick Tunnel 地址临时变化，不提供固定域名、访问白名单或生产级可用性保障。
- Docker daemon 必须由操作者先启动；当前机器上的 Docker CLI 检测到 daemon 未运行，因而镜像构建和实际公网联通只能在 daemon 可用后验证。
- 真实问数需要有效模型服务、Wren 数据源配置、已构建的语义模型和数据库只读权限；容器化本身不代表真实数据链路已验收。
- 不修改 Agent/Web 的业务 API 或认证逻辑；不把 Agent 端口、数据库端口或 Wren MCP 暴露到公网。
