# AskDB Agent

AskDB 是一个自然语言问数应用。第一次使用时，你只需要准备 AskDB 自身的 PostgreSQL 应用库，然后启动 Agent 和 Web；**业务数据库、业务表和数据由你自己准备，并在 Web 管理页面配置**。

[▶️ 查看 AskDB 演示视频](assets/askdb-demo.mp4)

## 项目结构

| 目录 | 用途 |
| --- | --- |
| `askdb-web/` | Next.js 聊天界面、登录页面和同源 API 代理 |
| `askdb-agent/` | FastAPI 服务、Agent 编排、数据源接入和 PostgreSQL 迁移 |
| `askdb-agent/src/integrations/migrations/` | AskDB 自身应用库的版本化建表/升级 SQL |

一次问数请求的主要路径：

```text
浏览器 → Next.js Web / BFF → FastAPI → LangGraph Agent → Wren 语义层 → 你配置的只读业务数据源
                         ←──────────── SSE 流式回答和结果 ────────────
```

管理员通过 Web 配置模型、数据源和语义模型，并给用户分配可访问的数据源。用户选择获授权的数据源后，用自然语言提问；AskDB 返回回答、表格和可用图表。查询使用只读数据源，图表编辑可先预览再应用，结果也可以导出为 CSV 或 PNG。

## 第一次启动

### 1. 安装本机依赖

- Git
- Python 3.11 或更新版本、[uv](https://docs.astral.sh/uv/)
- Node.js 22 或更新版本、pnpm
- PostgreSQL，以及已安装 `pgvector` 扩展文件的 PostgreSQL 服务

克隆仓库并进入项目目录：

```bash
git clone https://github.com/easonxu34-sys/ASKDB.git
cd ASKDB
```

### 2. 创建 AskDB 应用数据库

AskDB 用一个 PostgreSQL 数据库存放账号、会话、模型设置、数据源配置和记忆元数据。建议为它创建专用数据库和应用账号，不要把业务数据库当作 AskDB 应用库。

先用 PostgreSQL 管理员连接默认数据库并创建应用账号和数据库。下面用 `postgres` 作为管理员账号；如果你的管理员角色名称不同，请替换它：

```bash
psql -U postgres -d postgres
```

在 `psql` 中执行。`\password` 会交互式设置密码：

```sql
CREATE ROLE askdb_agent LOGIN;
\password askdb_agent
CREATE DATABASE askdb_agent_dev OWNER askdb_agent;
\q
```

再由 PostgreSQL 管理员连接刚创建的数据库，启用扩展：

```bash
psql -U postgres -d askdb_agent_dev
```

```sql
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
\q
```

如果服务器提示找不到 `vector` 扩展，先为当前 PostgreSQL 版本安装 [pgvector](https://github.com/pgvector/pgvector#installation)，再执行上面的命令。使用托管 PostgreSQL 时，可在服务商控制台或管理员 SQL 会话中启用该扩展。

### 3. 安装 Agent 并配置环境

```bash
cd askdb-agent
uv sync --locked
cp -n .env.example .env
```

生成 Fernet 加密密钥：

```bash
uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

编辑 `askdb-agent/.env`，至少填写应用数据库连接和刚生成的密钥：

```dotenv
ASKDB_DATABASE_DSN=postgresql://askdb_agent:your-url-encoded-password@127.0.0.1:5432/askdb_agent_dev
ASKDB_SETTINGS_ENCRYPTION_KEY=粘贴生成的Fernet密钥
```

把 `your-url-encoded-password` 换成创建数据库账号时设置的密码。密码中如果包含 `@`、`:`、`/` 等 URL 特殊字符，需要先进行 URL 编码。`.env` 已被 Git 忽略，**不要提交数据库密码、模型 API Key 或 Fernet 密钥**。Fernet 密钥用于保护 Web 中保存的模型凭证；丢失后，原有加密凭证无法解密。

如果希望直接使用 `.env.example` 中的默认模型，还需填写 `OPENAI_API_KEY`。也可以先启动应用，再登录 Web 管理页面配置模型。

### 4. 初始化管理员并启动 Agent

在 `askdb-agent/` 目录中，通过交互式终端创建首位管理员：

```bash
uv run askdb-agent auth init-admin
```

临时密码只显示一次，请立即保存。此命令只用于尚无账号的应用库。随后启动 Agent：

```bash
uv run uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

Agent 首次连接时会自动按迁移文件创建或升级 AskDB 应用表，无需手动导入建表 SQL。数据库应是为 AskDB 准备的空库；不要把已有业务表的数据库直接作为应用库。

### 5. 安装并启动 Web

另开一个终端，在仓库根目录执行：

```bash
cd askdb-web
pnpm install --frozen-lockfile
pnpm dev
```

打开 <http://localhost:3000/>。Web 默认将请求转发到本机 `http://127.0.0.1:8000` 的 Agent。Agent 健康检查地址为 <http://127.0.0.1:8000/healthz>。

## 登录后配置你自己的业务数据

1. 使用刚创建的管理员用户名和临时密码登录，并按提示修改密码。
2. 在 Web 管理页面配置模型。若已在 `.env` 配置默认模型，可检查并继续使用；也可在页面添加模型及其 API Key。
3. 在 Web 中添加你自己的业务数据源。填写可从运行 Agent 的机器访问的连接信息；数据库账号使用只读权限。
4. 为数据源准备并构建语义模型（MDL），然后为用户分配有权访问的数据源。
5. 用户进入聊天页面，选择已授权的数据源后即可开始提问。

仓库**不附带业务数据库、业务表或业务样例数据**。要查询真实业务数据，必须先由你准备数据库和表，再在 Web 中配置连接与语义模型。

## 日常开发

- 修改 Agent 后，保持 Agent 终端运行；`uvicorn --reload` 会在 Python 文件变化后重新加载。
- 修改 Web 后，保持 `pnpm dev` 终端运行；Next.js 会刷新开发页面。
- Agent 用户、应用表结构和 PostgreSQL 迁移细节见 [`askdb-agent/README.md`](askdb-agent/README.md)。
- 不要在应用数据库中存放业务表。需要联调时，在 Web 配置单独的只读业务数据源。

## 常见问题

| 现象 | 检查方式 |
| --- | --- |
| `ASKDB_DATABASE_DSN must configure PostgreSQL` | 确认 `askdb-agent/.env` 中的 `ASKDB_DATABASE_DSN` 已填写，并从 `askdb-agent/` 目录启动命令。 |
| 找不到 `vector` 扩展 | 确认 PostgreSQL 服务已安装与其版本匹配的 pgvector，并已在 `askdb_agent_dev` 中由管理员执行 `CREATE EXTENSION`。 |
| 登录后不能进行问数 | 在 Web 配置可用模型、只读业务数据源和语义模型，并确认当前用户获得该数据源授权。 |
| Web 无法连接 Agent | 先确认 Agent 正在运行，并能打开 `http://127.0.0.1:8000/healthz`。 |

停止开发服务时，在 Agent 和 Web 两个终端分别按 `Ctrl+C`。
