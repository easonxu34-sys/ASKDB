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
