# AskDB 多模型配置与会话选择实施计划

> **For agentic workers:** 按任务顺序在当前工作区执行；遵循 `docs/superpowers/specs/2026-10-02-global-model-settings-design.md`。

**Goal:** 管理多份加密模型配置，并允许每个会话选择独立模型。

**Architecture:** Agent 将原单行 SQLite 配置迁移为 profile 目录和默认 profile，按 profile ID 缓存模型运行时。Web 管理配置并在会话 metadata 中保存 `model_profile_id`；聊天请求只传 profile ID，Agent 只解析服务端目录。

**Tech Stack:** FastAPI、Pydantic、SQLite、Fernet、LangChain、Next.js、React、assistant-ui。

**Spec:** `docs/superpowers/specs/2026-10-02-global-model-settings-design.md`

## 全局约束

- 保留现有 `/v1/chat` SSE 事件结构和查询安全链路。
- API Key 只在 Agent 加密持久化；日志、响应、Web localStorage 中不得出现密钥。
- 聊天请求只接受服务端已配置的 `model_profile_id`，不接受模型名、endpoint 或凭据。
- 旧单模型数据库迁移后原密钥仍可解密；既有单数设置路由作为默认 profile 的兼容别名。
- 不运行测试或构建，除非用户明确要求验证。

## 任务

### 任务 1：Agent 多配置存储和单行数据迁移

- [x] 将 `ModelSettingsStore` 拓展为 profile CRUD、默认项读写和密钥清除。
- [x] 将旧 `model_settings` 行原子迁移到 `model_profiles`，复制密文并设为默认。
- [x] `.env` 首次初始化只创建一个默认 profile；公开投影不返回任何密钥数据。

### 任务 2：Agent 配置 API、运行时缓存和聊天模型路由

- [x] 增加目录、新增、更新、设默认、删除及按 profile 清除密钥接口。
- [x] 运行时按 profile ID 构建/缓存，配置变更只失效相关项；SSE 开始后固定当前 runtime 引用。
- [x] `/v1/chat` 接受可选 profile ID，拒绝未知 ID，默认缺省值解析为当前默认项。
- [x] 保留现有单数设置路由作为默认 profile 兼容层。

### 任务 3：Web BFF 和多配置管理界面

- [x] 新增 `/api/settings/models` BFF 路由并复用脱敏、no-store 行为。
- [x] 将设置对话框改为配置列表、新增/编辑、设默认、删除和清除密钥流程。
- [x] 所有保存都要求连接测试成功；用户输入密钥不写入浏览器持久存储。

### 任务 4：会话选择栏与请求绑定

- [x] 在 composer 输入框操作区显示可用 profile 选择栏。
- [x] 将所选 profile ID 写入当前会话 metadata/localStorage；新会话初始采用默认项。
- [x] 聊天 adapter 为对应 thread 提交 profile ID；配置被删除时提示并回退默认项。

### 任务 5：兼容和运维文档

- [x] 更新 Agent/Web API 与配置迁移说明，记录旧路径兼容行为和会话模型选择的本地存储范围。
- [x] 检查改动文件的接口名、schema 字段和文档契约保持一致。

实现与验证已完成（2026-10-02）：Agent `pytest` 33 项通过；Web Node 测试 10 项通过；`tsc --noEmit` 通过；Next.js 16.3.7 `next build --webpack` 通过。详见下方本次验证记录。

验证命令：

- Agent：`PYTHON_DOTENV_DISABLED=1 ASKDB_SETTINGS_ENCRYPTION_KEY= ASKDB_SETTINGS_DB_PATH=/private/tmp/askdb-agent-final-root-isolation-33.sqlite3 .venv/bin/python -m pytest -q`（`33 passed`）
- Web：`node --experimental-strip-types --test tests/*.test.mjs`（`10 passed`）
- Web：`../node_modules/.bin/tsc --noEmit`（退出码 0）
- Web：`../node_modules/.bin/next build --webpack`（Next.js 16.3.7，退出码 0）
