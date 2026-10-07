# 查询记忆召回提示实施计划

> **执行说明：** 在当前经用户授权的工作区内串行完成。下方复选框记录实施状态。

**目标：** 当已检索资料实际提供给 Agent，且本轮回答成功完成时，向用户显示准确、隐私安全的提示。

**架构：** 查询门禁返回 `READY` 且最终回答通过现有安全审查后，Agent 发出新增的 `memory_recall` SSE 事件，载荷按白名单资料类别统计数量，并附有边界受限的可展示详情。Web 适配器暂存载荷，仅在本轮成功结束后添加 `data-memory-recall` 消息部件；界面在回答下方显示单行摘要，点击后按类别展开详情。

**技术栈：** Python 3、FastAPI SSE、React 19、Next.js 16、TypeScript、assistant-ui。

**设计依据：** 2026-10-07 对话中确认的方案：说明资料已检索并提供给助手参考，不声称资料一定影响了模型回答；浏览器只接收用户认可的限长展示投影，不发送 SQL、匹配词或参考资料/数据源 ID。保留 SSE 既有会话与轮次元数据。

## 全局约束

- 只统计通过上下文令牌预算、实际传给查询门禁和回答 Agent 的资料。
- 澄清、内部请求拒绝、安全审查拒绝、失败、取消或未完成的轮次不显示提示。
- 类别只允许 `schema`、`business_rule`、`query_example`；详情仅包括最多 160 字标题，以及 Schema/业务口径最多 600 字纯文本正文，每轮最多 13 项。查询示例只显示自然语言标题；不传递 SQL、参考资料/数据源 ID 或匹配词。保留通用 SSE 包装层的既有轮次元数据。
- 保持授权、查询门禁和 `validate_read_query → dry_plan → dry_run → query` 流程不变。
- 更新聊天 SSE 契约文档和 Web 消费方。
- 当前任务没有要求测试；遵循仓库提示，不新增或运行测试，只执行允许的静态检查。

---

### 任务一：由 Agent 发出安全的召回元数据

**文件：**
- 修改：`askdb-agent/src/application/chat.py`

**接口：** `memory_recall` 事件载荷含 `counts` 和 `items`。计数只含白名单类别；详情项只含类别、限长标题和可选的限长纯文本详情。Schema/业务口径提供正文片段；查询示例只提供自然语言标题，不提供 SQL。仅在至少存在一条有效参考资料、最终回答通过现有安全审查后发出。

- [x] 从 `memory_references` 统计数量并生成有限详情；忽略未知类别和格式错误的条目。
- [x] `INTERNAL`、`CLARIFY`、门禁回退、没有最终回答和安全审查拒绝时不发事件。
- [x] 确认现有流包装层会为新增事件添加标准轮次元数据并执行撤销抑制检查，其他事件行为不变。

### 任务二：仅在成功的助手轮次中展示提示

**文件：**
- 新建：`askdb-web/lib/memory-recall-notice.ts`
- 新建：`askdb-web/components/assistant-ui/elements/memory-recall-notice.tsx`
- 修改：`askdb-web/lib/agent-chat-adapter.ts`
- 修改：`askdb-web/components/assistant-ui/elements/thread.aui.tsx`

**接口：** 适配器校验并暂存 SSE `counts` 与 `items`；只有收到 `done.status === "completed"` 后才添加 `{ type: "data", name: "memory-recall", data: { counts, items } }`。组件将计数保持为单行可点击摘要，展开后使用纯文本展示限长详情。没有新召回元数据的历史重放仍只显示文本。

- [x] 新增解析函数，只接受三个已知类别的正安全整数及有限标题/纯文本详情；无效数据不生成提示。
- [x] 暂存 `memory_recall`，等成功的 `done` 后展示；重放时清除暂存值，失败轮次不显示。
- [x] 在助手回答之后渲染可访问、低干扰的单行原生 disclosure；展开后按类别展示标题和限长纯文本详情。
- [x] 召回载荷和本地消息部件不包含 SQL、匹配词或参考资料/数据源 ID；通用流包装层保留既有轮次元数据。

### 任务三：更新 SSE 契约文档

**文件：**
- 修改：`docs/开发文档.md`

- [x] 记录新增的 `memory_recall` 事件、白名单计数/详情载荷和成功后单行展开提示的行为。

### 静态检查

- [x] 运行 `git diff --check`。
- [x] 对 Agent 源码运行 Python `compileall`；Web 使用可用的 lint、格式或类型检查命令，不运行测试。
- [x] 检查最终 diff，并记录历史服务端重放不会重建本次临时提示。

## 检查结果

- `askdb-agent/` 下运行 `.venv/bin/python -m compileall -q src` 通过。
- `askdb-web/` 下运行 `node_modules/.bin/tsc --noEmit --pretty false` 通过。
- `git diff --check` 通过。
- 对涉及的 Web 文件运行定向 `oxlint` 通过；`thread.aui.tsx:822` 有一条既存的 `no-useless-spread` 警告。
- 新增的两个 Web 文件通过定向 `oxfmt --check`。`pnpm run lint` 被仓库范围的 `oxfmt --check` 格式问题阻断，共报告 53 个文件；两个已修改 Web 文件的 `HEAD` 版本也无法通过相同格式检查。
- 根据当前仓库提示，没有新增或运行测试。
- 服务端轮次历史只保存助手文本，因此历史重放和跨设备会话不会重建这个临时提示。
- 本次浏览器交互未做端到端人工验证。
