# ASKDB 本机打包与公网运行设计

## 目标

将公网演示从 Docker 镜像切换为 Mac 宿主机本地构建和生产进程，同时保留 Tailscale Funnel 的 HTTPS `:8443` 地址、Agent/Web 安全边界以及现有 Docker 部署中的账号、设置和 Wren 数据。

## 运行结构

- Agent/Web 源码复制到 `~/Library/Application Support/ASKDB-Agent/app` 后本机打包，launchd 不直接执行 macOS 受保护的 `Documents` 目录。Agent 使用独立 Python 环境，按 `uv.lock` 安装非 editable wheel，只监听 `127.0.0.1:8001`。
- Web 在 Application Support 源码副本中按 `pnpm-lock.yaml` 安装依赖并执行 `pnpm exec next build --webpack`，Next production server 只监听 `127.0.0.1:3001`，Agent 上游为 `http://127.0.0.1:8001`。
- Tailscale Funnel `https://<host>:8443` 只转发到 Web。Web 启动时取得 Funnel origin 并设置 `ASKDB_WEB_ORIGIN`，保持 Origin/CSRF 校验契约。
- Python 环境、Agent/Web 运行源码和构建产物、Agent 数据、密钥、launchd 配置与日志统一保存在权限受限的 `~/Library/Application Support/ASKDB-Agent`。本机开发 Agent 仍可用原 `askdb-agent/data` 与 `8000`，两套进程和数据互不覆盖。
- 日常 `start-test.sh` / `stop-test.sh` 不依赖 Docker。Compose 和 Dockerfile 保留为可选开发/回退环境，不构建镜像。

## 从旧 Docker 部署迁移

1. `migrate-docker-data-local.sh` 校验旧 Compose 项目，然后停止其 Web 与 Agent，避免 SQLite/Wren 文件在复制时继续变化。
2. 从现有 Agent 容器的 `/app/data` 导出到 Application Support 下的私有临时目录，检查目录非空，并对 `model-settings.sqlite3` 执行 SQLite `quick_check`。
3. 将临时目录原子改名为 `~/Library/Application Support/ASKDB-Agent/data`；仅在该副本中把 SQLite 所保存的 `/app/data/wren/...` 路径重写为本机绝对路径，再次执行完整性检查。原 Compose 容器和 `agent_data` 卷都保留；迁移失败时删除未完成的本机副本，并尽量恢复迁移前正在运行的 Compose 服务。
4. 新 Agent 使用原 `.env.docker` Fernet key 解密迁移过来的设置。启动时将 key 写入权限为 `0600` 的 Application Support `secrets.env`。如果 `.env.local` 同时存在，必须与 `.env.docker` 中的 key 相同。key 只传给 Agent/管理命令，不传给 Web，也不写入日志。

## 生命周期与失败处理

- 当前用户 GUI launchd 域以固定标签托管 Agent/Web；Binary plist 位于 Application Support 的 `run`，日志位于 `logs`，相关目录权限为 `0700`，配置、密钥和日志文件权限为 `0600`。launchd 入口脚本位于 Application Support 的源码副本，不从 `Documents` 启动。
- 启动前确认指定 launchd 标签未加载；端口冲突时只报错，不按端口杀进程。
- 停止时只从当前用户 GUI 域卸载这两个部署标签。停止会关闭 Funnel，但保留本机数据和日志。
- Agent/Web 健康检查失败时卸载本次刚加载的 launchd 服务、关闭本次 Funnel 配置，并报告日志路径，不把日志内容或密钥自动打印到终端。
- launchd 后台服务会脱离启动脚本的命令会话；用户注销时停止，重新登录后由操作者再次启动。
- 旧 Docker 数据卷保留作回退副本。回退时停止本机服务，再启动旧 Compose Agent/Web；同一数据副本不能被两套 Agent 同时写入。
- 普通重启同步仓库源码到 Application Support，并重新构建；不会删除持久数据。密钥只在明确配置不一致时停止，不静默替换已存密钥。

## 验收

- 启动/停止脚本不调用 Docker，不构建或下载应用镜像。
- Agent/Web 依锁文件在宿主机完成安装与生产构建；Agent 健康检查和 Web 首页均就绪。
- `lsof` 确认 Agent 仅监听 `127.0.0.1:8001`，Web 仅监听 `127.0.0.1:3001`；Funnel 公网入口为 HTTPS `:8443`，不直达 Agent。
- 旧卷迁移检查覆盖非空目录、SQLite 完整性、旧卷保留、同一 Fernet key，以及失败时 Compose 服务恢复。
- 健康检查成功不等价于公网登录、SSE 聊天或真实只读查询完成端到端验收。

## 运行前提

需要 macOS、Python 3.13、uv、Node.js 22 或更新版本、pnpm、Tailscale CLI；部分数据库连接器还依赖主机编译工具或数据库客户端库。依赖版本由锁文件约束，但 macOS 与 Docker Linux/CPU 架构会选择不同的原生 wheel 和动态库；本方案固定使用本机已验证的运行环境。
