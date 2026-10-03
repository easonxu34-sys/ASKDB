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

## 当前状态

UI 已切换为调用 Agent 的流式适配器；Agent 实现了 SSE API、只读 SQL 检查和 Wren 查询前的 dry-plan / dry-run 门禁。TypeScript、生产构建和 10 项 Python 测试通过。端到端真实数据查询还需配置 Wren 项目/MDL、MySQL 只读账号和模型服务。
