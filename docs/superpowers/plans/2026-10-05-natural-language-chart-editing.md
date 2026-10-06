# 自然语言图表编辑实施计划

> **实现者说明：** 按任务依赖逐项实施并勾选 `- [ ]`。优先使用 `superpowers:executing-plans` 工作流；只有用户或适用规范明确要求时才委派给子 Agent。

**目标：** 为现有图表增加安全的自然语言编辑、共用的图表配置提交路径、有界撤销历史，以及需要新查询时的显式确认流程。

**架构：** 增加一个经过认证的意图解释端点；模型调用使用严格输出 schema 且不绑定工具。复用现有 runtime model，不另建带工具的 `create_agent`。Web 校验并应用展示补丁，且与手动编辑器共用持久化路径。需要查询的意图只有在用户确认后才转成普通聊天查询。

**技术栈：** Python 3、FastAPI、Pydantic、LangChain chat model、TypeScript、React 19、Next.js 16、localStorage artifact helpers、Node 内置 test runner、pytest。

**设计稿：** `docs/superpowers/specs/2026-10-05-natural-language-chart-edit-design.md`

## 全局约束

- 不要为图表编辑解释器绑定 Wren 查询、图表渲染或其他工具。
- 不要将查询行或 SQL 发送到图表编辑意图解释请求。
- 图表编辑意图解释端点不得执行查询。
- 已确认的查询操作必须走现有、已认证的 `/api/chat` 和 `/v1/chat` 链路。
- 所有展示补丁必须使用允许字段，并在持久化前通过图表/查询字段校验。
- 手动编辑和自然语言编辑必须共用图表视图提交路径。
- 解析、校验或缓存写入失败时，之前已保存的图表状态必须保持不变。
- 图表 override 和撤销历史必须限定在现有用户/thread/turn/result 缓存范围内，并遵守其大小上限。
- 不得改变现有只读 SQL gate、Wren 规划顺序或图表产物安全边界。
- 浏览器提交的所有图表上下文都视为不可信的意图解释输入；不能作为线程所有权、数据源授权、结果身份或模型配置访问权限的证据。
- 不得把模型生成的查询文本发给聊天接口。确认后的查询消息必须在可见的 `/api/chat` 消息中使用用户原始指令，并带明确的图表请求前缀。
- 不要在 `build_graph` 中构造解释器；从已获取的 runtime model 延迟创建/调用解释器，避免结构化输出不兼容影响普通聊天。

## 任务依赖关系

- Task 0 是兼容性门槛，必须先于解释器/API 开发。
- Task 1 定义共享意图与策略契约；Task 2 依赖 Task 1；Task 3 依赖 Task 1 和 Task 2。
- Task 4 依赖 Task 1 中确定的意图到视图映射。契约冻结后，可与 Task 2–3 并行。
- Task 5 依赖 Task 4 最终确定的视图/override 结构；Task 6 依赖 Task 3–5；Task 7 在全部实现任务之后执行。
- Python domain/API schema 与 TypeScript 视图/历史契约分别维护；不要跨语言导入，也不要让 API schema 依赖 React 组件。

---

### Task 0：验证 runtime 与模型结构化输出兼容性

**文件：**
- 新建：`docs/superpowers/reviews/2026-10-05-chart-edit-model-compatibility.md`（provider/model 能力矩阵和决策记录）
- 检查：`askdb-agent/src/integrations/models.py`
- 检查：`askdb-agent/src/application/model_settings.py`
- 检查：`askdb-web/package.json` 和仓库 Node 版本配置

**接口与决策：**
- 按启用的 provider/model 配置记录结构化输出支持情况。现有 profile 探测只调用普通 `ainvoke`，不能证明 `with_structured_output` 可用。
- 定义统一 adapter 契约：要么返回已校验的结构化结果，要么抛出明确的不支持/provider 错误；不得降级到无约束文本或 JSON 解析。
- 确认执行导入 TypeScript 模块的 `.mjs` 测试所用 Node 版本。`package.json` 当前没有 test script 或 `engines` 声明。
- 不把 VMind、Chat2Plot 或 LIDA 作为运行时依赖：VMind 的 README 将对话式编辑标为开发中，且输出 VChart spec；Chat2Plot 面向 Python/Plotly/Altair；LIDA 执行模型生成的可视化代码。调研依据和边界见设计稿“开源方案复用评估”。

- [x] 验证模型封装对 OpenAI、DeepSeek 和自定义/OpenAI-compatible 配置暴露的 API 形态，并覆盖调用时拒绝的情况（仅成功构造并不能证明 endpoint 支持）。
- [x] 在条件允许时，对已配置的 provider/model profile 发起有界且无副作用的 schema 请求；记录不支持的组合，并为这些组合禁用图表意图解释。
- [x] 选择/固定或记录受支持的 Node 测试版本。下文的 `--experimental-strip-types` 命令需要 Node 22.6 或更新版本；不能假设 Next 16 的最低 Node 版本自然包含此 flag。
- [x] 不要增加启动时的 eager model 探测，也不要借此改变普通模型 profile readiness 语义。

### Task 1：定义图表编辑意图和确定性策略

**文件：**
- 新建：`askdb-agent/src/domain/chart_edit.py`
- 新建：`askdb-agent/src/application/chart_edit.py`
- 新建：`askdb-agent/tests/test_chart_edit.py`

**接口：**
- `ChartPaletteToken`：严格枚举 `blue`、`teal`、`green`、`amber`、`orange`、`red`、`purple`、`slate`，Python domain 和 TypeScript 视图契约分别定义相同的 token 集。
- `ChartEditContext`：包含 `source_result_id`、`view`、`columns`、`column_types`、`row_count` 和 `truncated`；不含 SQL、行值或工具。上下文来自客户端，仅在提交前针对本地查询/图表产物再次校验。
- `ChartEditPatch`：只包含允许的部分视图字段；嵌套标签/格式/度量颜色映射按字段增量合并。不得包含 `current_result_top_n`、饼图类别颜色、结果身份、ECharts 选项或任意样式数据；Top N 和类别颜色分别由类型化操作设置。
- `ChartEditQueryOperation`：枚举 `database_filter`、`full_data_top_n`、`aggregation` 和 `period_comparison`；界面固定说明按枚举生成。
- `ChartEditClarificationCode`：枚举 `top_n_scope_required`、`top_n_metric_required`、`source_unit_required`、`field_not_in_result`、`category_not_in_result`、`category_ambiguous`、`conflicting_category_color`、`chart_type_incompatible`、`conflicting_sort` 和 `operation_unsupported`；Web 按 code 映射固定文案及字段/类别选项。类别不在结果中或标签歧义由 Web 对真实结果做二次校验后产生。
- `ChartEditIntent`：严格区分状态的结果类型，包含 `status`、可选 `patch: ChartEditPatch`、可选 `current_result_operation: { kind: "top_n"; field: string; count: int; direction: "asc" | "desc"; scope: "current_result" }`、可选最多八项的 `category_color_operations: Array<{ category_label: string; color: ChartPaletteToken }>`、可选 `query_proposal: { operation: ChartEditQueryOperation }` 和可选 `clarification: { code: ChartEditClarificationCode }`。类别颜色操作仅面向饼图；Web 使用真实查询结果解析类别标签。不得含模型生成的摘要、查询原因或澄清文本。
- `classify_chart_edit(instruction, intent, context) -> ChartEditIntent`：确定性策略接收原始指令，拒绝无效字段，只映射已支持的展示操作，并强制把筛选、聚合、周期对比和全量 Top N 归为 `query_required`。

- [ ] 为所有有效 `status` 增加 domain 测试；拒绝额外 key、格式错误的枚举、缺失必填项及相互冲突的状态。
- [ ] 校验状态约束：`apply` 必须有补丁、当前结果操作或类别颜色操作之一，且不能带查询/澄清字段；`query_required` 必须有允许的查询提议，可暂存展示操作；`clarify` 必须有澄清 code，且不能带操作。
- [ ] 增加策略测试，证明已有字段的展示修改保持本地处理，Top N 只能标记为 `current_result`，数据库筛选/按月聚合/同比/全量 Top N 必须查询。
- [ ] 通过测试明确补丁语义：标量/列表替换；标签/格式/颜色映射只合并本次提供的字段键；未提供的键保留原视图；只能通过类型化的当前结果操作设置 `current_result_top_n`。
- [ ] 增加歧义测试：未说明 Top N 范围、度量缺失/隐藏/未选中、源单位未知、Top N 与排序冲突，都应返回 `clarify`，不得猜测编辑结果。
- [ ] 为饼图类别颜色意图增加测试：模型只返回用户指令中的类别显示标签和固定 token；Web 必须把标签唯一解析到当前查询结果的类型化类别值。不存在的标签、多个类别共用同一显示标签、同一类别收到冲突颜色都产生澄清，且不能部分修改配置。
- [ ] 验证类别配色意图只允许用于当前饼图；Web 必须用当前 query artifact 验证饼图约束和类别匹配，不能依赖 Agent 对客户端上下文的判断。
- [ ] 验证没有源单位元数据时，“改成万元”会要求澄清；用户明确给出源单位和目标单位时，计算出的摘要会可见地注明该单位是用户声明的。
- [ ] 强制 Top N 数量为 1–100，只能选当前已选中且可见的数值度量，稳定保持输入顺序处理并列值，并明确 `current_result` 范围。V1 支持柱状图；饼图仅在现有饼图约束已满足时支持；折线图需澄清，因为度量排名会破坏时间顺序。操作范围仅限已返回的行；Web 将其映射为 `current_result_top_n` 和匹配的度量排序。
- [ ] 度量单位必须来自可信元数据或用户明确声明的源单位和目标单位；不能由模型根据字段名/值推断。
- [ ] 按图表类型定义配色行为：柱状图/折线图使用度量配色；V1 饼图支持逐类别配色，但只接受固定 palette token 和当前结果中唯一匹配的类别标签，不向模型发送类别值或查询行。
- [ ] 实现严格 Pydantic models，设置 `extra="forbid"` 并限制面向用户文本的长度。
- [ ] 将确定性分类与模型意图解释分开实现，数据充分性不能由模型判断决定。
- [ ] 在 `askdb-agent/` 下运行 `uv run pytest tests/test_chart_edit.py -q`，确认新增 domain 测试通过。

### Task 2：复用现有模型，增加无工具的图表编辑解释器

**文件：**
- 新建：`askdb-agent/src/agent/chart_edit_interpreter.py`
- 新建：`askdb-agent/tests/test_chart_edit_interpreter.py`

**接口：**
- `ChartEditInterpreter(model).ainvoke(instruction, context) -> ChartEditIntent`。
- application 用例通过 `lease.snapshot.graph.model` 延迟构造/调用解释器；`AgentRuntime` 与 `build_graph` 保持不变。
- 解释器不绑定任何工具，只输出 domain intent schema。

- [ ] 使用 fake model 测试，证明解释器只收到指令、视图、字段名/类型、行数和截断状态。
- [ ] 断言 SQL、查询行、Wren toolkit 和工具定义都不会进入解释器输入。
- [ ] 测试格式错误的模型输出、模型异常和 provider 不支持结构化输出；每种情况都要返回明确且有界的安全失败，不能触发查询工具。
- [ ] 只对 Task 0 确认支持的组合实现结构化模型输出；所有输出经 `ChartEditIntent` 和 `classify_chart_edit` 校验。
- [ ] 将指令、字段名、标签和视图上下文视为有界的不可信数据；测试类似指令的列名/标签不能改写系统提示词、schema 或工具策略。
- [ ] 让解释器/application 用例与 `AgentRuntime` 和 `create_agent_for_turn()` 分离；普通查询 Agent 的构造不受解释器兼容性影响。
- [ ] 在 `askdb-agent/` 下运行 `uv run pytest tests/test_chart_edit_interpreter.py -q`。

### Task 3：提供经过认证的意图解释 API 和 Web 代理

**文件：**
- 新建：`askdb-agent/src/api/schemas/chart_edit.py`
- 新建：`askdb-agent/src/api/routes/chart_edits.py`
- 修改：`askdb-agent/src/api/app.py`
- 新建：`askdb-agent/tests/test_chart_edit_api.py`
- 新建：`askdb-web/app/api/chart-edits/interpret/route.ts`
- 新建：`askdb-web/lib/chart-edit-request.mjs`
- 新建：`askdb-web/tests/chart-edit-request.test.mjs`
- 新建：`askdb-web/tests/chart-edit-api-route.test.mjs`

**接口：**
- Agent 路由：`POST /v1/chart-edits/interpret`。
- Web 路由：`POST /api/chart-edits/interpret`，使用现有 Agent session proxy 模式认证。
- Python 请求模型为 `ChartEditRequest`，响应为 domain `ChartEditIntent`。Web 从 `chart-edit-request.mjs` 导出 `sanitizeChartEditRequest()` 和 `readChartEditIntent()`，并在分支处理前再次校验响应。
- 请求含 `thread_id`、可选的 `model_profile_id`、原始指令、源结果 ID、图表视图、字段名/类型、行数和截断标记。不含 `data_source_id`、SQL、查询行、工具或聊天历史字段；未知 key 一律拒绝。数据源只能从已认证线程的服务端绑定中派生。
- 请求限制：body 最大 64 KiB，指令最长 2,048 字符，最多 100 列，列名最长 128 字符，类型名最长 64 字符，标题最长 120 字符，字段标签最长 80 字符；thread/profile ID 沿用现有长度限制。无效数值/数量必须拒绝，不能强制转换。
- 响应为已校验的 `ChartEditIntent`，`category_color_operations` 最多八项，`category_label` 最长 128 字符；响应头设置 `Cache-Control: no-store`。不得返回可直接展示的模型摘要、查询原因或澄清问题。

- [ ] 增加请求 schema 测试，覆盖缺少 ID、指令超长、字段过多、列/类型格式错误、未知属性，以及显式拒绝 `data_source_id`、`sql`、`rows`、`tools` 或聊天历史。
- [ ] 在 BFF 和 Agent schema 两侧都执行上述请求边界校验，且必须在模型调用前完成。
- [ ] 增加 API 测试，覆盖未认证、线程不存在/未绑定、所有者不匹配、授权撤销、绑定数据源已禁用及模型配置无效。验证数据源从服务端绑定派生，授权检查发生在解释前，且请求不会创建/重绑线程。
- [ ] 验证线程所选模型配置传入 `acquire_runtime(source_id, model_profile_id)`，且解释器使用 `lease.snapshot.graph.model`，而非默认模型或新建模型。
- [ ] 测试解释成功、provider 异常、超时和请求取消时 lease 都会释放；测试获取失败时不调用 release。
- [ ] 增加路由测试，证明 `query_required` 只返回查询提议，不调用 `stream_chat_response`、Wren 工具或 SQL 执行。
- [ ] Agent 路由使用当前 principal 和已有服务端 thread/source 绑定，重新检查 grant。不得信任客户端图表上下文作为授权依据，也不得在这个只解释、不查询的端点中持久化新绑定。
- [ ] HTTP 身份/绑定/runtime/错误映射留在 route；意图解释和策略编排放在 `application/chart_edit.py`。
- [ ] 将结构化输出不兼容与聊天 runtime readiness 区分开：返回稳定、安全的错误 code，禁止回退到非结构化模型输出。
- [ ] 在 `api/app.py` 注册路由。
- [ ] 增加 Web 请求清理器和轻量同源代理路由，使用现有认证 Agent session、CSRF 校验、body 大小限制、`no-store` 和安全错误映射；绝不向浏览器暴露 Agent token。
- [ ] 测试 BFF 的 CSRF/认证拒绝、body 上限、Agent 路径与授权头精确转发、`no-store` 和安全上游错误映射；确认凭证及 provider 内部错误不会返回给客户端。
- [ ] 在 `askdb-agent/` 下运行 `uv run pytest tests/test_chart_edit_api.py -q`。
- [ ] 在仓库根目录、使用 Task 0 确认的 Node 版本运行：`node --experimental-strip-types --test askdb-web/tests/chart-edit-request.test.mjs askdb-web/tests/chart-edit-api-route.test.mjs`。

### Task 4：扩展共用图表配置，支持颜色和当前结果 Top N

**文件：**
- 修改：`askdb-web/lib/chat-output.ts`
- 修改：`askdb-web/lib/chart-output.ts`
- 修改：`askdb-web/components/assistant-ui/elements/chart-result.tsx`
- 修改：`askdb-web/tests/chat-output.test.mjs`
- 修改：`askdb-web/tests/chart-output.test.mjs`

**接口与行为：**
- 新增固定 `ChartPaletteToken` 联合类型（V1 token 集：`blue`、`teal`、`green`、`amber`、`orange`、`red`、`purple`、`slate`），并在 `ChartViewConfiguration` 中增加可选 `color_by_metric` 映射与可选饼图 `pie_category_colors: { dimension_field: string; by_category_key: Record<string, ChartPaletteToken> }`。
- `by_category_key` 使用稳定的类型化类别键（例如将 `[typeof value, value]` 规范序列化），而不是可碰撞的显示标签；仅对 `string | number | boolean` 类别值生成键。类别显示标签不能唯一映射回类别值时拒绝该编辑。
- 在 `chart-output.ts` 提供并复用纯函数 `chartCategoryKey(value)` 和 `resolvePieCategoryLabel(query, dimensionField, label)`：后者返回唯一匹配、未找到或歧义结果，让 API flow、校验和图表编辑器使用相同的安全匹配规则。
- 在图表视图配置中新增可选的 `current_result_top_n: { field: string; count: number; direction: "asc" | "desc" }`。
- `validateChartView()` 校验颜色 token；Top N 字段必须是当前已选中且可见的数值度量，count 为 1–100 的整数。柱状图可用；饼图仅在现有规则本已通过时可用；折线图不可用。Top N 生效时，度量排序和方向必须与之匹配。手动改用其他排序、切换折线图，或选择操作隐藏/移除排名字段时，应在同一次提交和摘要中清除 Top N。
- `validateChartView()` 还要验证饼图颜色映射的维度、类型化类别 key、token 和条目数量；配置中不能出现当前源结果没有的类别 key。
- `buildEChartsOption()` 对关联结果前 1,000 行应用 Top N，数值并列时稳定保留原索引顺序；不修改查询产物中的行。界面标明范围为当前结果，并在任一截断信号为真时显示警告。
- 固定 palette token 映射到柱状图/折线图度量系列；饼图按切片设置 `itemStyle.color`，颜色映射通过维度字段和类型化类别键匹配。没有显式颜色的类别使用按源结果首次出现顺序决定的默认调色板，使排序变化不会导致颜色换到其他类别。切换维度字段时清除旧维度类别配色并纳入同一提交摘要。
- 手动图表编辑器和自然语言编辑共用饼图类别配色配置/提交校验；手动 UI 为当前饼图结果中的每个可见类别提供固定 palette 选择。
- 应用 Top N 时，将 `sort` 规范化为相同度量和方向；自然语言排序冲突时拒绝猜测并要求澄清。手动改为不同排序、不兼容的图表类型或移除/隐藏排名字段时，在同一保存视图中清除 Top N，并将清除行为写入摘要。

- [ ] 测试有效/无效 palette token、逐类别饼图配色、未知/歧义类别拒绝、类型化类别键（字符串 `"1"` 与数值 `1` 不冲突）、默认颜色排序稳定、维度切换后清除类别映射；同时测试 Top N 字段不存在/隐藏/未选中/非数值，折线图拒绝，现有饼图约束，count 边界及大于可用行数，排序冲突，手动排序/切换类型/字段变更后清除 Top N，并覆盖稳定并列、1,000 行上限及只对已返回行执行 Top N。
- [ ] 测试类别配色意图请求不携带 query rows 或类别值清单；模型输出中的类别标签只由本地 resolver 与实际结果匹配。
- [ ] 测试 `query.truncated`、报告行数大于返回行数、返回行数超过 1,000 三种截断信号；不得将任一种情况展示成全量排名。
- [ ] 扩展 `validateChartView`、`chartViewsEqual`、回放解析和 option 构造逻辑，不修改查询产物行。
- [ ] 验证只修改一个字段的标签/格式/度量颜色或一个饼图类别颜色时，其他字段及类别的全部现有配置都保留。
- [ ] 保持 chart artifact schema 不变；将新增项加到 `ChartViewConfiguration`，并升级 `ChartViewOverride.schema_version`，保留 v1 读取路径。
- [ ] 运行：`node --experimental-strip-types --test askdb-web/tests/chat-output.test.mjs askdb-web/tests/chart-output.test.mjs`。

### Task 5：统一手动/自然语言提交，并持久化有界撤销历史

**文件：**
- 新建：`askdb-web/lib/chart-edit-history.mjs`
- 修改：`askdb-web/lib/local-thread-adapter.tsx`
- 修改：`askdb-web/components/assistant-ui/elements/chart-result.tsx`
- 新建：`askdb-web/tests/chart-edit-history.test.mjs`
- 修改：`askdb-web/tests/thread-result-artifacts.test.mjs`

**接口：**
- `commitChartViewChange({ userId, threadId, historyTurnId, sourceResultId, before, after, summary, origin })` 是唯一成功的图表视图提交路径。它检查存储视图仍等于 `before`，且指定 turn 中匹配的查询与图表产物仍存在。
- `undoChartViewChange({ userId, threadId, historyTurnId, sourceResultId })` 在一次原子缓存写入成功后才恢复前一视图并移除最新历史记录。
- 当前 override 和最多 20 条历史记录在同一次 `localStorage` artifact-index 写入中序列化。写入失败时，存储状态和可见状态都不变。
- 旧版 v1 override 读取为视图且撤销历史为空。无效历史不能应用；产物缺失、过期、淘汰或不兼容时，该结果不可编辑/撤销。

- [ ] 为纯存储逻辑增加测试，覆盖追加、20 条淘汰、result/thread/turn 隔离、v1 读取、before 过期拒绝、缓存淘汰/过期/产物缺失、撤销和 localStorage quota/容量超限写入失败。
- [ ] 重构手动编辑器，令其使用共用提交函数，同时保持原有预览/取消行为；只有共用提交确认写入成功后才更新可见 React state。
- [ ] 撤销操作在一次写入中恢复前一视图；解析、校验、序列化或缓存写入失败时，保持旧视图/历史/UI 状态。
- [ ] 验证结果缓存淘汰时，其 view override 和历史一起删除；不能将撤销状态另存到独立 localStorage key。
- [ ] 使用不同 UI 文案区分“撤销”和“恢复 AI 推荐”。
- [ ] 验证“恢复 AI 推荐”会生成一条新的共用提交/历史记录，而且可以再次撤销；不能把它实现成 `undoChartViewChange()` 的别名。
- [ ] 运行：`node --experimental-strip-types --test askdb-web/tests/chart-edit-history.test.mjs askdb-web/tests/thread-result-artifacts.test.mjs askdb-web/tests/chat-output.test.mjs`。

### Task 6：增加自然语言 UI、摘要、澄清和查询确认流程

**文件：**
- 修改：`askdb-web/components/assistant-ui/elements/chart-result.tsx`
- 修改：`askdb-web/components/assistant-ui/elements/thread.aui.tsx`
- 修改：`askdb-web/lib/agent-chat-adapter.ts`
- 修改：`askdb-agent/tests/test_chat.py`
- 新建：`askdb-web/lib/chart-edit-flow.mjs`
- 修改：`askdb-web/tests/chat-request.test.mjs`
- 新建：`askdb-web/tests/chart-edit-flow.test.mjs`

**接口与流程：**
- 自然语言提交带当前图表/结果上下文调用 `/api/chart-edits/interpret`。
- 图表编辑区允许为本次意图解释临时选择一个可用模型，默认沿用当前会话模型；选择通过 `model_profile_id` 传递，不写入会话偏好。模型目录不可用时禁止退回服务端默认模型。需要查询并经用户确认的后续 `/api/chat` 请求继续使用当前会话模型。
- `status=apply`：校验并合并补丁，通过 `commitChartViewChange()` 提交，再根据已保存的前后视图计算摘要并显示撤销操作。
- `status=clarify`：将 clarification code 映射成本地固定文案，并根据已校验的当前查询字段生成选项；不得展示模型编写的文本或修改图表。
- `status=query_required`：显示允许的操作、由固定文案生成的原因以及实际待发送的聊天消息。只有用户明确确认后，才通过现有 `/api/chat` 流程发送，并携带当前线程 CSRF token、所选 model profile、`turn_id`、`expected_sequence` 及现有幂等重试/恢复行为。
- 确认消息由原始用户指令加明确的“生成图表”请求标记组成。确认前逐字预览该消息，确保 `_chart_requested_for_turn()` 选择图表生成。不得发送模型生成的查询文本。
- 混合查询/编辑意图暂存在本次页面流程的局部状态中，关联旧结果 ID 和确认后的 `turn_id`。只有 turn 成功完成，且成功的查询/图表 `source_result_id` 一致，并且所有字段在新查询结果上通过校验后，才应用暂存编辑；查询/turn 失败、取消、缺少图表、ID 不匹配或字段不兼容时丢弃暂存内容。
- 所有终止路径都清理暂存状态；不得把它存入模块级可变 map，也不得在校验/提交成功前持久化。
- chat adapter/流程须向暂存编辑的所有者提供确认 turn 的终止状态及成功查询/图表产物 ID；不要增加或重新解释 SSE 事件名/payload 来实现本地关联。
- `chart-edit-flow.mjs` 中的 `interpretChartEdit()` 和 `confirmChartEditQuery()` 隔离响应分支，便于不挂载 React 组件直接测试。

- [ ] 增加流程测试，证明 `apply` 不调用 `/api/chat`，`clarify` 不持久化视图，`query_required` 在用户明确确认前不调用 `/api/chat`。
- [ ] 实现并测试纯异步流程 helper：`interpretChartEdit(intent, { commit, clarify, showQueryProposal })` 和 `confirmChartEditQuery(proposal, originalInstruction, sendChat)`；`sendChat` 委托给现有 adapter。
- [ ] 增加 adapter/flow 测试，证明暂存编辑所有者能收到现有 `done` 成功/失败状态及匹配的结果/图表产物 ID；不得扩展外部 SSE 契约。
- [ ] 增加混合操作测试，证明补丁在 stream 成功结束前一直暂存，仅在查询/图表 ID 匹配时应用；查询/turn 失败、取消、无图表、结果 ID 错误或字段不兼容时丢弃。
- [ ] 增加混合查询/类别配色测试：新结果中类别缺失或标签不唯一时，整组暂存编辑都不提交，旧图表和视图保持不变。
- [ ] 增加类别配色流程测试：类别未知、同显示名对应多个类型值、冲突颜色或饼图规则不满足时显示固定澄清文案，绝不写入部分配置。
- [ ] 测试确认消息中包含 `_chart_requested_for_turn()` 能识别的标记，且保留原始指令；确认没有模型生成的自由格式查询被发送。
- [ ] 使用 Agent 侧 `_chart_requested_for_turn()` 增加单元测试，证明准确的确认消息会启用图表生成，而普通的纯展示编辑不会启动 Agent chat turn。
- [ ] 将自然语言输入放在现有图表编辑控制旁的醒目位置，并提供示例、处理中和错误状态。
- [ ] 从已校验的变更计算精确前后摘要，不信任模型文本；验证 API 契约不含自由格式摘要或澄清文本。
- [ ] 增加查询确认界面，仅在用户显式确认动作中调用现有 chat adapter。
- [ ] 意图解释或查询处理中防止重复提交；每个请求关联源结果 ID、远端 thread、history turn 和确认后的 turn ID。验证并发或过期提议不会应用到更新的图表结果。
- [ ] 运行：`node --experimental-strip-types --test askdb-web/tests/chart-edit-flow.test.mjs askdb-web/tests/chat-request.test.mjs askdb-web/tests/chat-output.test.mjs`。
- [ ] 在 `askdb-agent/` 下运行 `uv run pytest tests/test_chat.py -q`，验证现有图表请求检测器。

### Task 7：运行重点集成验证并审查改动

**文件：**
- 检查：`askdb-agent/tests/test_chart_edit.py`
- 检查：`askdb-agent/tests/test_chart_edit_interpreter.py`
- 检查：`askdb-agent/tests/test_chart_edit_api.py`
- 检查：`askdb-agent/tests/test_chat.py`
- 检查：Task 0 的 provider/model 兼容矩阵及未变更的普通 `ainvoke` profile 探测
- 检查：`askdb-web/tests/chart-edit-request.test.mjs`
- 检查：`askdb-web/tests/chart-edit-api-route.test.mjs`
- 检查：`askdb-web/tests/chart-edit-history.test.mjs`
- 检查：`askdb-web/tests/chart-edit-flow.test.mjs`
- 检查：`askdb-web/tests/chart-output.test.mjs`
- 检查：`askdb-web/tests/chat-output.test.mjs`

- [ ] 运行以上 Agent 重点测试文件。
- [ ] 运行以上 Web Node 重点测试文件。
- [ ] 运行 `git diff --check` 并检查完整 diff，确认复用现有 `/api/chat`/`/v1/chat` 授权与查询安全链路，没有新增 SQL 路径、放宽只读 gate 或修改 Wren 查询工具。
- [ ] 根据测试验证：意图解释和展示编辑不会启动聊天 turn 或查询；显式确认才会启动查询。
- [ ] 如有依赖或环境限制，明确记录，不能声称端到端完成。
