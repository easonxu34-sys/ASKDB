# ASKDB-Agent 本地 Docker 公网测试部署设计

## 状态

已按用户选择更新为 Cloudflare 命名隧道。参考脚本最初采用 Quick Tunnel；该模式不支持 SSE，不能满足 AskDB 聊天链路，因此改用可承载 SSE 的命名隧道。

## 目标

在当前 ASKDB-Agent 仓库提供一套可重复的本地 Docker Compose 部署：容器运行 Agent 与生产模式 Web，由 Cloudflare 命名隧道把 Web 映射到用户配置的公网域名。

## 方案

Compose 包含三个服务：

- `agent`：基于 `askdb-agent/` 构建，监听容器内 `8000`。不发布宿主机端口。
- `web`：基于 `askdb-web/` 构建，运行 Next.js production server，监听容器内 `3000`，并通过 `ASKDB_AGENT_URL=http://agent:8000` 调用 Agent。可选地只绑定 `127.0.0.1:3000` 供本机检查。
- `tunnel`：使用 cloudflared 容器连接用户在 Cloudflare 控制台创建的命名隧道，已发布路由指向 Compose 内的 `http://web:3000`。公网主机名由用户的 Cloudflare 域名决定，SSE 通过该隧道到达浏览器。

浏览器请求始终进入 Web 的同源 API 路由。Agent API、Wren CLI、SQLite 和数据源凭证都留在 Compose 网络/本地持久卷，不直接映射到公网。

## 持久化与密钥

- Agent 的 `/app/data` 使用 Compose 命名卷，保存 SQLite 账号/模型设置、加密记忆、Wren 配置和 Wren 数据源修订。
- 仓库中的 `wren-project/` 以只读方式提供给 Agent 作为现有语义项目模板；运行时生成的数据留在命名卷。
- 提供不含真实凭据的 Docker 环境模板，要求操作者本地复制并填写模型配置、Fernet 加密密钥、Cloudflare 隧道 Token 和公网主机名；实际 `.env` 不提交 Git。
- 不设置通用管理员账号或固定密码。首次启动后通过 `docker compose exec -it agent askdb-agent auth init-admin` 交互式生成管理员临时密码。
- `stop-test.sh` 仅停止本 Compose 项目并保留持久卷；删除卷的重置操作不属于普通停止流程。

## 仓库交付物

- 根目录 Compose 文件，定义内部服务网络、环境变量、健康检查和数据卷。
- Agent 与 Web 各自的 Dockerfile，使用锁文件构建依赖并以生产命令启动。
- Docker 环境变量示例及 `.gitignore` 例外规则。
- 参考 `start-test.sh` / `stop-test.sh` 的启动与停止脚本：启动时等待服务就绪并显示配置的公网主机名；停止时只操作本 Compose 项目。
- 根 README 与 Agent README 中的 Docker 首次启动、管理员初始化、Wren 数据源准备、停止和数据保留说明。

## 操作流程

1. 在 Cloudflare 控制台创建命名隧道及 Web 发布路由，目标为 `http://web:3000`；安装并启动 Docker Desktop，准备 Docker 环境文件。
2. 执行启动脚本构建并启动 Compose；脚本输出本机检查地址与配置的公网地址。
3. 在 Agent 容器交互终端初始化管理员，再从公网 Web 页面登录并按要求修改临时密码。
4. 按产品现有流程配置模型和数据源。数据库连接仍使用数据库层只读账号。
5. 执行停止脚本关闭 Web、Agent 和隧道，保留 Agent 数据卷。

## 验收

- Compose 配置可解析，Agent 与 Web 镜像可构建。
- Web 容器能通过服务名访问 Agent；Agent 不存在宿主机或公网发布端口。
- 启动脚本在容器就绪后显示配置的公网主机名，cloudflared 连接已创建的命名隧道，停止脚本仅停止当前 Compose 服务且不删除卷。
- 管理员初始化仍然要求交互终端，脚本和模板不包含默认账号密码或 API Key。
- 重启容器后 Agent SQLite/Wren 数据仍保留。
- 公网访问只到 Web，登录使用 HTTPS Cookie；公开链接持有者可访问登录页面，因此不得填入生产凭据或用未经授权的数据源。

## 边界与前提

- 本方案是在本地运行应用、经 Cloudflare 命名隧道公开 Web 的测试部署。它依赖 Cloudflare 账号、Cloudflare DNS 中的域名、隧道 Token 和已发布路由。
- Docker daemon 必须由操作者先启动；当前机器上的 Docker CLI 检测到 daemon 未运行，因而镜像构建和实际公网联通只能在 daemon 可用后验证。
- 真实问数需要有效模型服务、Wren 数据源配置、已构建的语义模型和数据库只读权限；容器化本身不代表真实数据链路已验收。
- 不修改 Agent/Web 的业务 API 或认证逻辑；不把 Agent 端口、数据库端口或 Wren MCP 暴露到公网。
