# askdb-agent

AskDB 的 Python Agent 服务，目标链路为 LangGraph → WrenToolkit → Wren Core → 只读数据库。

当前目录已包含 FastAPI SSE API、LangGraph runtime 和受控 Wren 查询工具。真实问数服务启动前，需要准备 Wren CLI 项目并绑定 profile，构建 `target/mdl.json`，再配置可用的模型服务。WrenToolkit 依赖已准备好的 Wren 项目；仅安装 Python 依赖不会自动连接数据库。

## 环境要求

- Python 3.11 或更新版本
- uv
- 已安装的 Wren CLI（Agent 依赖会安装 PostgreSQL、MySQL、BigQuery、Snowflake、ClickHouse、Trino、SQL Server、Databricks、Redshift、Oracle、Athena 等 connector；DuckDB 包含在 Wren core 中，Doris 使用 Wren 的 MySQL connector）
- 已初始化并构建 MDL 的 Wren 项目，且项目显式绑定只读 profile
- 可供 LangChain 使用的模型服务凭证

## 安装依赖

```bash
uv sync --locked
cp .env.example .env
```

编辑 `.env`，填写本机 Wren 项目目录和模型服务信息。默认使用 DeepSeek OpenAI 兼容接口及 `deepseek-v4-flash` 模型名；API Key 只放本机 `.env` 或密钥管理服务，不要提交凭证。Wren 数据源页面按锁定的 Wren 版本提供数据库/数仓 connector 表单。本文后续 `askdb_mysql` 命令仅用于现有 MySQL 示例项目。

模型目录支持多份 OpenAI 兼容模型配置；用户可在每个会话的输入框中选择模型。账号、模型配置、Wren 数据源、会话与记忆元数据统一保存在 `ASKDB_DATABASE_DSN` 指向的 PostgreSQL 数据库中；模型 API Key 和 Wren 凭据仍由 Fernet 加密。生成一把 Fernet 密钥并通过部署密钥管理系统注入 `ASKDB_SETTINGS_ENCRYPTION_KEY`：

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

通过 `ASKDB_DATABASE_DSN` 配置 PostgreSQL 连接；部署密钥不要提交到仓库。启动时 Agent 会把仓库内的版本化 PostgreSQL schema 应用到目标库。开发环境可将 DSN 和 Fernet key 写入本机 `.env`；生产环境应由密钥管理系统提供。数据库备份和 Fernet key 必须一并保护；只有数据库密文而没有原 key 无法恢复 API Key。

### PostgreSQL 迁移与 pgvector

Agent 按 `askdb-agent/src/integrations/migrations/NNN_name.sql` 的版本顺序初始化和升级数据库。已执行版本及 SQL 文件 SHA-256 校验和写在 `app_schema_migrations`；启动只应用尚未记录的迁移。已有 `001_initial` 数据库会继续升级，新数据库首次启动会依次创建完整 schema；校验和变化、缺少历史版本或未受管理的现存表会使启动失败，避免静默覆盖结构。添加迁移时使用下一个连续编号，并保留已发布的 SQL 文件不变。

PostgreSQL 服务器必须先安装与其主版本匹配的 pgvector 扩展文件，并在每个目标数据库中由数据库管理员启用一次。普通 Agent 数据库角色不应设置为超级用户。新建数据库后、Agent 首次启动前，用管理员连接执行：

```sql
CREATE EXTENSION vector WITH SCHEMA public;
```

例如 Homebrew PostgreSQL 18 可先执行 `brew install pgvector`；其他安装方式见 [pgvector 官方安装说明](https://github.com/pgvector/pgvector#installation)。之后 Agent 的 `002_enable_pgvector` 迁移会验证或在管理员运行迁移时启用该扩展。当前应用尚未直接读写向量列，因此不依赖 Python `pgvector` 适配包。

### 持久会话记忆与在线召回

持久会话记忆和在线召回默认启动，无需设置启用开关。首次启动时，Agent 在 `ASKDB_AGENT_DATA_DIR` 下创建独立的 `agent-memory/` 目录，自动生成并以 `0600` 权限保存稳定的 Fernet journal 密钥，同时创建加密删除 journal。journal 和语料文件仍是独立文件工件，不写进 PostgreSQL；启动时会先应用数据库迁移、校验 journal、恢复状态并清理过期记录，再接受会话请求。

必须把 `data/agent-memory/` 和语料目录放在持久卷中，并与 PostgreSQL 一起备份。密钥丢失或更换会使已加密的 journal 无法恢复。在线召回只使用已绑定并激活的语料；gold 评测与 runtime-ready 环境变量不再是启动门槛。若需关闭在线召回，可设置 `ASKDB_AGENT_RECALL_ENABLED=0`。也可通过 `ASKDB_MEMORY_JOURNAL_PATH`、`ASKDB_MEMORY_JOURNAL_KEY` 或 `ASKDB_AGENT_MEMORY_CORPUS_DIR` 覆盖默认存储位置/密钥，生产环境建议从部署密钥管理器注入稳定密钥。

如果尚无 PostgreSQL 模型记录但没有配置 Fernet 密钥，现有 `.env` 模型仍可用于聊天，设置 API 会返回 `MODEL_SETTINGS_UNAVAILABLE`。数据库已有加密设置而密钥缺失或不匹配时，Agent 会失败关闭，不回退到 `.env` 模型。

首次启动且 PostgreSQL 没有模型目录时，Agent 从 `ASKDB_MODEL`、`OPENAI_BASE_URL`、`OPENAI_API_KEY` 初始化一个默认模型配置。聊天请求只传 profile ID，Agent 从 PostgreSQL 解析模型详情。密钥轮换目前需在维护窗口停止 Agent，使用旧 Fernet 密钥解密后再用新密钥重加密；若旧密钥丢失，需重新录入模型 API Key。

会话的模型选择保存在浏览器按用户 ID 分区的本地会话 metadata 中，不含 API Key，也不会在不同浏览器间同步。旧的匿名本地会话 key 会保留，但登录后不再读取；既有 thread 未保存选择时采用当前默认 profile。

## 本地账号与登录

AskDB 使用 Agent 本地账号，Agent 是用户身份、角色、会话和数据源授权的权威方。账号、会话、登录限流、模型/Wren 设置和记忆元数据都使用同一个 PostgreSQL 数据库。表结构由版本化迁移初始化。这个 demo 版本的 runtime 注册表按单 Agent 实例运行设计；多个 Agent 进程不能共用单机 runtime 状态。

完成依赖配置后，在 Agent 主机上首次启动前交互式创建唯一的初始管理员：

```bash
cd askdb-agent
uv run askdb-agent auth init-admin
```

命令要求使用交互式终端；用户名由操作者输入，临时密码由系统生成并仅在终端显示一次。请立即通过组织内部渠道交付。没有默认账号、默认密码或公开 bootstrap API；此命令只会在尚无账号时成功一次。

若所有管理员都无法登录，在 Agent 主机上使用受控恢复命令；它会按输入的现有用户名/工号将账号恢复为启用管理员、撤销此账号的所有旧会话并生成新的临时密码。操作者必须交互确认，用户首次登录后仍需更改密码：

```bash
uv run askdb-agent auth recover-admin
```

此本地运维命令不开放 HTTP 接口，也不能读取旧密码。日常用户忘记密码时，仍由管理员通过 Web 用户管理页重置。

然后按“启动”一节启动 Agent 和 Web。管理员首次登录必须更换临时密码。之后管理员可在 Web 用户管理页创建、禁用或重新启用账号、修改用户名和角色、重置密码，并将启用的数据源分配给普通用户。每次创建或重置后的临时密码只显示一次；用户下次登录必须修改密码。管理员可以聊天并访问所有启用的数据源；普通用户只能聊天，并且只能访问分配给自己的启用数据源。账号禁用、密码重置和角色变化会撤销该用户的现有会话。

新设密码至少 8 个字符，不要求大小写、数字或符号组合。登录与改密页面的密码输入框默认隐藏，可通过眼睛按钮切换显示；首次登录强制改密时，临时密码字段始终隐藏且不提供切换按钮。

Web 使用 HttpOnly、Secure、SameSite=Lax 的会话 Cookie；生产部署必须使用 HTTPS。仅在本机 loopback 开发环境允许 Web 或 Agent URL 使用 HTTP，非 loopback Agent 地址必须配置 HTTPS。不要把 Agent API 直接暴露到公网。Web 的模型设置、Wren 设置和账号管理入口受管理员权限保护，Agent API 仍会独立验证登录会话和角色。不要依赖前端隐藏按钮作为访问控制。

登录失败限流只按规范化账号标识执行，不读取 `X-Forwarded-For` 或其他客户端可伪造的转发头。若未来需要按来源 IP 限流，必须先明确可信代理及其覆盖头配置，再单独实现。

登录账号、会话、模型设置和数据源配置共享同一 PostgreSQL 数据库。备份/恢复时应同时保护 PostgreSQL 备份、Fernet key、独立删除 journal 和记忆语料；丢失 PostgreSQL 会同时丢失账号、会话和配置。应用写事务通过 PostgreSQL advisory lock 串行执行，并以唯一部分索引作为单会话运行轮次的额外约束。

Wren 项目至少应完成：

```bash
uv run wren profile add askdb_mysql --datasource mysql --interactive
uv run wren context init --path ../wren-project
uv run wren context set-profile askdb_mysql --path ../wren-project
uv run wren profile debug askdb_mysql
```

在 `../wren-project` 中按目标库实际 schema 编写 `models/`、`views/` 与业务描述后，再运行：

```bash
cd ../wren-project
uv run --project ../askdb-agent wren context validate
uv run --project ../askdb-agent wren context build
uv run --project ../askdb-agent wren profile debug askdb_mysql
```

`profile debug` 必须确认连接使用 MySQL 只读账号。不要把数据库凭证放进 Wren project 文件或 Git。profile 凭证应由 Wren CLI 的受支持密钥机制管理。

## 启动

### 查询图表

聊天可根据成功的只读查询结果生成折线图、柱状图或饼图。请求中明确写出“折线图”/`line chart`、“柱状图”/`bar chart` 或“饼图”/`pie chart` 时使用对应类型；“不要饼图”等否定表达不会触发饼图。未指定图表类型时，时间维度加数值指标选择折线图，其他分类维度加数值指标选择柱状图。饼图要求一个分类字段、一个数值指标且最多 8 个分类；每个图最多使用 4 个数值系列和查询返回的最多 1,000 行。

图表只引用本轮 `wren_query` 已成功执行的查询结果，不会为作图执行额外 SQL。无法绘制时仍保留查询表格和回答，并说明图表不可用。

终端一（Agent）：

```bash
cd askdb-agent
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

终端二（Web）：

```bash
cd askdb-web
npm run dev
```

Web 端默认通过 Next.js `/api/chat` 转发至 `http://127.0.0.1:8000/v1/chat`。若端口不同，在 `askdb-web/.env.local` 设置 `ASKDB_AGENT_URL`。

## 本机本地公网部署

公网演示直接在 Mac 上按 `uv.lock`、`pnpm-lock.yaml` 构建 Agent 与 Web，不构建 Docker 镜像。完整首次配置、Tailscale Funnel、服务端口和运行边界见[根目录 README](../README.md#本机本地构建--tailscale-funnel-公网演示)。Agent 绑定 `127.0.0.1:8001`，Web 绑定 `127.0.0.1:3001`，只有 Web 经 Funnel HTTPS `:8443` 对外提供访问。本机 Agent 可直接连接宿主机数据库的 `localhost`/`127.0.0.1`。

本机部署先复制仓库根目录 `.env.local.example` 为 `.env.local` 设置 Fernet key，再复制 `askdb-agent/.env.example` 为 `askdb-agent/.env` 设置 `ASKDB_DATABASE_DSN`。本地启动脚本会从 Agent 模块的 `.env` 读取 DSN。源码与构建产物放在 `~/Library/Application Support/ASKDB-Agent/app`；PostgreSQL 是唯一应用数据库，本机 `data` 目录只存放 Wren 文件、删除 journal 和记忆语料，日志、密钥和 launchd 配置也放在该私有目录中。

本地打包完成后，在有交互 TTY 的终端初始化首位管理员：

```bash
scripts/askdb-agent-local.sh auth init-admin
```

仅在没有账号时初始化；临时密码只显示一次，没有默认账号或密码。需要受控恢复管理员时运行并按提示确认：

```bash
scripts/askdb-agent-local.sh auth recover-admin
```

导入的 PostgreSQL 数据包含原账号时无需重复初始化。之后在 Web 设置页面配置模型、数据源和只读数据库账号，构建语义模型并分配用户数据源权限。

`scripts/stop-test.sh` 只卸载本部署的 launchd 服务，关闭 Tailscale Funnel，并保留 `~/Library/Application Support/ASKDB-Agent/data`。服务由当前用户的 launchd 会话托管；用户注销时会停止，重新启动公网服务需再次运行 `scripts/start-test.sh`。备份时同时保护整个数据目录和原 Fernet key。旧 Compose 与 Dockerfile 仅作为可选开发/回退材料；确认本机部署正常前不要删除旧 `agent_data` 卷。

## 当前进度

API 和 LangGraph runtime 已实现。用户登录、会话、角色授权和用户数据源授权的部署及运行说明见本节；完整开发顺序、SSE 契约和端到端验收条件见 [`docs/开发文档.md`](../docs/开发文档.md)。
