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

模型目录支持多份 OpenAI 兼容模型配置；用户可在每个会话的输入框中选择模型。配置保存在 `ASKDB_SETTINGS_DB_PATH` 指向的 SQLite 文件中，并用 Fernet 加密每份 API Key。生成一把 Fernet 密钥并通过部署密钥管理系统注入 `ASKDB_SETTINGS_ENCRYPTION_KEY`：

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

开发环境可将密钥写入本机 `.env`；生产环境不要把密钥写进仓库镜像或 SQLite 卷。默认数据库路径为 `./data/model-settings.sqlite3`，实际默认位置在 Agent 包目录下的 `data/model-settings.sqlite3`；可通过 `ASKDB_SETTINGS_DB_PATH` 指定持久化卷。Agent 会限制新建数据目录为 `0700`、数据库文件为 `0600`。备份时同时保护 SQLite 文件和 Fernet 密钥；只有数据库密文而没有原密钥无法恢复 API Key。

### 启用持久会话记忆

持久会话记忆默认关闭。要在本机启用，在 `.env` 中配置 `ASKDB_AGENT_MEMORY_ENABLED=1`、`ASKDB_MEMORY_JOURNAL_PATH` 和稳定的 `ASKDB_MEMORY_JOURNAL_KEY`；journal 目录必须与设置数据库目录、语料目录分开。可将语料目录指定为 `ASKDB_AGENT_MEMORY_CORPUS_DIR`。开发环境可生成一把 Fernet 密钥并保存到本机 `.env`：

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

示例路径：`ASKDB_MEMORY_JOURNAL_PATH=./data/agent-memory/deletion-journal.jsonl`，`ASKDB_AGENT_MEMORY_CORPUS_DIR=./data/agent-memory-corpus`。从 Agent 项目目录启动时，这两个目录会分别与默认设置数据库目录隔离。密钥必须跨重启保留；丢失或更换密钥会使已加密的 journal 无法恢复。配置完成后重启 Agent，启动会先运行迁移、journal 校验、恢复和过期清理，再接受会话请求。

如果尚无 SQLite 模型记录但没有配置 Fernet 密钥，现有 `.env` 模型仍可用于聊天，设置 API 会返回 `MODEL_SETTINGS_UNAVAILABLE`。数据库已有加密设置而密钥缺失或不匹配时，Agent 会失败关闭，不回退到 `.env` 模型。

首次启动且 SQLite 没有模型目录时，Agent 从 `ASKDB_MODEL`、`OPENAI_BASE_URL`、`OPENAI_API_KEY` 初始化一个默认模型配置。原有单行 `model_settings` 记录会迁移为默认 profile，并保留其密文；之后 SQLite 配置目录优先于 `.env`。聊天请求只传 profile ID，Agent 从 SQLite 解析模型详情。密钥轮换目前需在维护窗口停止 Agent，使用旧 Fernet 密钥解密后再用新密钥重加密；若旧密钥丢失，需重新录入模型 API Key。

会话的模型选择保存在浏览器按用户 ID 分区的本地会话 metadata 中，不含 API Key，也不会在不同浏览器间同步。旧的匿名本地会话 key 会保留，但登录后不再读取；既有 thread 未保存选择时采用当前默认 profile。

## 本地账号与登录

AskDB 使用 Agent 本地账号，Agent 是用户身份、角色、会话和数据源授权的权威方。账号、会话、登录限流和用户数据源授权与模型/Wren 设置共用 `ASKDB_SETTINGS_DB_PATH` 指向的 SQLite 文件。首次受保护请求会幂等创建 auth 表；Wren 设置初始化会给 thread 数据源绑定表增加可空 `owner_user_id`，不回填或认领旧行。这个 demo 版本按单 Agent 实例运行设计；不要让多个独立 Agent 实例各自使用不同的数据库文件。

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

登录账号、会话、模型设置和数据源配置共享同一 SQLite 文件。备份/恢复时应沿用现有 SQLite 与 Fernet 密钥的保护策略，并同时保留数据库文件和 Fernet 密钥；丢失数据库会同时丢失本地账号、会话和配置。SQLite 账号/会话方案未验证多 worker 或多实例并发部署。

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

## 当前进度

API 和 LangGraph runtime 已实现。用户登录、会话、角色授权和用户数据源授权的部署及运行说明见本节；完整开发顺序、SSE 契约和端到端验收条件见 [`docs/开发文档.md`](../docs/开发文档.md)。
