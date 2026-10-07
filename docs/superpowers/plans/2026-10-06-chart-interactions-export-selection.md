# 图表交互、标注、导出与选区追问实施计划

> 当前状态：自然语言修改图表功能已于 2026-10-06 移除。选区追问仍只填充现有聊天输入框，由用户检查并主动发送。

> **给开发 Agent：**按 `superpowers:executing-plans` 逐项实施并逐项验证。只有用户明确要求时才委派子 Agent。每项使用 checkbox 跟踪。

**目标：**在当前聊天查询结果图表上增加缩放/指示线、参考标注与有范围说明的统计、图片和 CSV 导出，以及基于已返回行的点选/框选追问。

**架构：**继续由 Web 使用同一轮 `QueryResultArtifact` 生成白名单 ECharts option，不新增独立查询链路。把 ECharts 实例生命周期与事件映射放在专用 Canvas 组件，把稳定的查询行索引、统计和导出编码放在纯函数模块；`ChartResult` 负责界面交互与当前图表配置保存。查询选区只通过用户可见的聊天输入发送，不能由 ECharts 事件直接启动查询。

**技术栈：**Next.js 16、React 19、TypeScript、ECharts 6.1.0、现有 per-thread result artifact/`ChartViewOverride` 缓存、Node `node:test`。

**设计依据：**本计划实现 [图表交互编辑器首版设计](../specs/2026-10-04-interactive-chart-editor-design.md) 的同结果、Web 展示层和本地覆盖项边界，并延续 [ECharts 图表工具设计](../specs/2026-10-04-echarts-chart-tool-design.md) 的结果来源和白名单 option 约束。本计划定义前述设计未覆盖的缩放、标注、导出和选区追问行为。

## 全局约束

- 仅基于图表绑定的成功查询结果操作。任何持久化配置、点击、标注或追问都必须继续校验 `source_result_id` 与当前 `queryArtifact.resultId` 一致。
- Web 不持有 Agent token、模型密钥、数据库凭证；本功能不直接调用数据库、不产生 SQL、不绕过 `validate_read_query → dry_plan → dry_run → query`。
- 绘图和 CSV 只用已返回的 `rows`。当前 Agent 单次查询上限是 1,000 行；`truncated=true` 表示“可能不完整”，不能作为完整总体的证明。完整性文案统一三态：可能截断、查询端未标记截断、完整性未明确；后两者也不得宣称覆盖整个业务总体。
- 统计先在原始十进制值上计算，再按当前图表格式显示；不得从 ECharts 浮点绘图值反推统计结果。不同单位的指标分别计算和标注。
- ECharts option 由 Web 白名单代码构造。不得接收或合并模型生成的任意 option、JavaScript、HTML tooltip 或导出文件名。
- 对旧 `ChartViewOverride` 做兼容读取；当前 reader 接受 schema 1–3，新保存使用 schema 4；旧版本缺少新标注配置时规范化为空数组，不丢弃有效的既有配置和撤销历史。
- 不增加 npm 依赖，不修改 SSE 事件或结果 artifact 的服务器契约，除非实施前发现无法通过现有用户可见聊天输入完成选区追问；若确需扩展契约，先在实现报告中说明最小字段和安全校验，再做兼容性改动。
- 执行前阅读仓库 `AGENTS.md` 和 `askdb-web/AGENTS.md`。编写本计划期间，`main` 从 `f72a3fe` 前进到 `4960c7c feat: enhance chart editing and conversation workflows`，先前已暂存的 chart-edit 改动现已在 HEAD 中；本计划没有创建该提交。当前状态快照另有不属于本功能的 `askdb-web/app/assistant.tsx`、登录与设置 UI 修改、`app/favicon.ico` 删除，以及未跟踪的 `app/icon.svg` 和 `components/brand/`；执行时重新记录状态，不覆盖、暂存或纳入这些改动。不得 reset、stash 或重新暂存任何既有改动；仅当用户明确要求时才提交。

---

## 文件职责图

| 文件 | 本计划中的职责 |
| --- | --- |
| `askdb-web/components/assistant-ui/elements/chart-result.tsx` | 图表卡片的交互状态、工具栏、标注编辑、统计展示、选中行详情和用户确认后的追问发送。避免把 ECharts 生命周期、CSV 编码和纯统计算法继续堆入这个大组件。 |
| `askdb-web/components/assistant-ui/elements/chart-canvas.tsx`（新建） | 初始化/销毁 ECharts 实例、更新 option、resize、注册/清理 `click`/`datazoom`/`brushSelected` 监听，并提供重置缩放和 PNG 下载接口。编辑器预览使用非交互模式。 |
| `askdb-web/lib/chart-output.ts` | 从经过校验的查询结果和视图配置生成稳定的展示行、ECharts option 和标注；导出 `getChartCompletenessState(query)`：当 `truncated=true`、返回计数不匹配或 rows 超出图表处理上限时返回 `possibly_incomplete`；明确 `truncated=false` 且计数一致时返回 `not_marked_truncated`；其他情况返回 `unknown`。展示行携带查询结果中的原始行索引。 |
| `askdb-web/lib/chart-interactions.ts`（新建） | 纯函数：把 ECharts 数据项/框选事件映射到原始行索引，归一化选区，生成可见且有大小上限的追问上下文。 |
| `askdb-web/lib/chart-statistics.ts`（新建） | 纯函数：按“已返回结果”或“当前图表窗口”计算每个指标的均值/峰值及有效样本数。 |
| `askdb-web/lib/chart-export.ts`（新建） | 纯函数：CSV 单元格转义、公式注入防护、文件名安全化和 CSV 内容生成。 |
| `askdb-web/lib/chart-decimal.ts` | 如现有精确十进制工具不足，增加平均值/比较所需的精确运算；不改变现有数值显示行为。 |
| `askdb-web/lib/chat-output.ts` | 扩展 `ChartViewConfiguration` 中的可选标注配置、校验/默认值、相等判断和 `ChartViewOverride` 版本读取。 |
| `askdb-web/lib/local-thread-adapter.tsx` | 仅在新配置无法由现有 override/undo 通用逻辑承载时调整持久化；保证图表配置仍按当前用户/会话/轮次/结果 ID 隔离。 |
| `askdb-web/tests/chart-output.test.mjs`、`chat-output.test.mjs`、`thread-result-artifacts.test.mjs`（已有） | 覆盖 option、配置兼容、持久化和撤销。 |
| `askdb-web/tests/chart-interactions.test.mjs`、`chart-statistics.test.mjs`、`chart-export.test.mjs`（新建） | 覆盖事件映射、范围统计、导出安全和输入上限。 |

不预先修改 `askdb-agent/`、`askdb-web/lib/agent-chat-adapter.ts` 或 Agent API。普通追问由现有 composer 和聊天链路承接；选区只填入可编辑草稿，由用户检查并主动发送。

---

## Task 0：确认执行 checkout 与现有改动边界

**文件：**只读 `AGENTS.md`、`askdb-web/AGENTS.md`、本计划中列出的图表文件及它们的 staged/unstaged diff；不修改业务文件。

- [x] **步骤 1：记录工作区。**运行 `git status --short --branch` 和 `git worktree list --porcelain`，记录当前分支、暂存/未暂存/未跟踪项。
- [x] **步骤 2：检查同范围改动。**分别查看 `git diff --cached -- askdb-web/components/assistant-ui/elements/chart-result.tsx askdb-web/lib/chart-output.ts askdb-web/lib/chat-output.ts askdb-web/lib/local-thread-adapter.tsx` 与不带 `--cached` 的同路径 diff；只为理解当前状态，不重置或改写。
- [x] **步骤 3：核实真实入口和依赖。**确认 `ChartResult → EChartCanvas → buildEChartsOption`、`ChartViewOverride` 的读取/保存/undo 路径、`queryArtifact` 的 `rows/rowCount/truncated` 字段，以及当前锁文件中的 ECharts 版本。不要依赖旧设计替代当前实现。
- [x] **步骤 4：确认 checkout 包含哪些改动。**本计划撰写时的 `main` HEAD 为 `4960c7c`，其中包含 chart-edit 与确认发送流程。若开发 Agent 使用的 checkout 不含该提交，不得假设 `query_required` 流程已存在；基于实际 checkout 复核聊天 composer/查询授权流程，不从其他 worktree 静默复制或覆盖文件。

**完成条件：**形成简短执行基线记录，区分已有改动与本次改动；没有任何既有改动被更改或重新暂存。

**执行基线记录（2026-10-06）：**原工作区 `main` 为 `4960c7c` 且有与本任务无关的 UI 改动及本计划未跟踪文件；图表目标文件无 staged/unstaged diff。实施在从 `4960c7c` 建立的隔离 worktree `codex/chart-interactions`。真实路径为 `ChartResult → 内嵌 EChartCanvas → buildEChartsOption`；option 当前变化会销毁并重建 ECharts 实例。通用 `commitThreadChartViewChange` / `undoThreadChartViewChange` 使用 `validateChartView` 保存和撤销；query artifact 有 `rows`、可选 `rowCount`/`truncated`，ECharts 锁定 6.1.0。未改代码前执行四个现有定向测试文件共 51 项，43 通过、8 失败；失败断言与当前图表颜色/编辑行为不一致，后续验证须与此基线比较，不计作本任务引入。

**基线失败明细（实施前与最终复跑相同）：**原四文件命令 `node --experimental-strip-types --test askdb-web/tests/chart-output.test.mjs askdb-web/tests/chat-output.test.mjs askdb-web/tests/thread-result-artifacts.test.mjs askdb-web/tests/chart-edit-flow.test.mjs` 最终为 57 项、49 通过、8 失败；8 个失败测试为 `chart-edit-flow` 的 `applies strict display edits by merging nested maps and defers no-op or invalid edits`、`manual and interpreted type changes share normalization and preserve compatible colors`；`chart-output` 的 `builds pie options from the exact categorical and numeric rows`、`validates and displays exact decimal pie values and shares`、`maps fixed metric and pie category palettes to stable typed categories`；`chat-output` 的 `ignores unknown versions and invalid or mismatched overrides`、`validates fixed metric palettes and rejects CSS colors or pie metric colors`、`validates typed pie category keys and detects missing or ambiguous labels`。断言期望旧色板 token（如 `"purple"`、小写 HEX）或旧编辑结构，实际返回当前 HEAD 已有的 `ChartColorSpec` / 新编辑语义。包含本次新增测试的扩展定向套件共 77 项、69 通过、8 失败；失败仍为同一组 8 项，因此新增测试全部通过且没有新增失败。

---

## Task 1：稳定行映射和 ECharts Canvas 事件边界

**文件：**

- 新建 `askdb-web/components/assistant-ui/elements/chart-canvas.tsx`
- 新建 `askdb-web/lib/chart-interactions.ts`
- 修改 `askdb-web/lib/chart-output.ts`
- 修改 `askdb-web/components/assistant-ui/elements/chart-result.tsx`
- 新建 `askdb-web/tests/chart-interactions.test.mjs`
- 修改 `askdb-web/tests/chart-output.test.mjs`

**接口：**

- `chart-output.ts` 导出 `deriveChartRows(query, view)`；结果为按现有排序和 `current_result_top_n` 投影后的 `{ row, sourceRowIndex }[]`，`sourceRowIndex` 始终指向 `query.rows` 原始位置。
- `chart-canvas.tsx` 接受 `option`、`sourceKey`、`interactive`、`onPointClick`、`onBrushSelection`、`onDataZoom` 和 `className`。回调只输出稳定的 `sourceRowIndex`、系列索引以及当前轴窗口，不把整份查询结果交给 ECharts。
- Canvas ref 提供 `resetZoom()` 和 `downloadImage(filename)`。实例初始化/销毁只跟随组件生命周期或 `sourceKey`，option 更新使用 `setOption`；事件监听绑定一次并在销毁时清理。
- `chart-result.tsx` 只把 `interactive=true` 用于正式图表卡片；编辑器预览关闭数据选取和 brush。

- [x] **步骤 1：先写行映射测试。**在 `chart-output.test.mjs` 验证原序、排序、Top N 后 `sourceRowIndex` 仍指向来源行；重复分类不得合并。运行目标 Node 测试，确认新断言失败。
- [x] **步骤 2：实现统一行投影。**把当前 `sortRows`/Top N 路径收敛到 `deriveChartRows`，让图表 series、tooltip、点击和框选都使用相同投影；不在各回调内重复排序。
- [x] **步骤 3：定义并测试事件归一化。**在 `chart-interactions.ts` 将 ECharts `dataIndex`/brush `dataIndex` 映射到 `sourceRowIndex`；过滤非 series、非法索引、重复索引和超出当前结果的索引。输入源必须是当前 `sourceKey` 对应的投影行。
- [x] **步骤 4：抽出 Canvas 并保持实例稳定。**保留动态导入、`ResizeObserver`、渲染失败回退和 dispose；option 更新不得无故重复初始化实例。任何回调都需在 effect cleanup 中解绑。
- [x] **步骤 5：验证事件边界。**运行 `node --experimental-strip-types --test askdb-web/tests/chart-interactions.test.mjs askdb-web/tests/chart-output.test.mjs`。检查失败渲染不影响原查询结果表格，预览 Canvas 不生成交互回调。

**完成条件：**从任意图表数据项可以稳定定位到当前结果 artifact 的原始行；事件处理不执行网络请求；图表更新和卸载不泄漏监听器或实例。

---

## Task 2：区间缩放、十字指示线和复位

**文件：**

- 修改 `askdb-web/lib/chart-output.ts`
- 修改 `askdb-web/components/assistant-ui/elements/chart-canvas.tsx`
- 修改 `askdb-web/components/assistant-ui/elements/chart-result.tsx`
- 修改 `askdb-web/tests/chart-output.test.mjs`
- 修改 `askdb-web/tests/chart-interactions.test.mjs`

- [x] **步骤 1：添加 option 测试。**确认折线图和纵/横柱状图具有针对维度轴的 `dataZoom`：内部缩放可用，类别数量超过 30 时出现 slider；饼图不生成 `dataZoom` 或 `axisPointer`。确认轴向 tooltip 同一类别下读取全部可见指标。
- [x] **步骤 2：生成缩放与指示线配置。**在统一 option builder 中生成 `inside` 与条件 slider；折线/纵柱默认使用 x 维度轴，横柱使用 y 维度轴。设置 `tooltip.trigger = "axis"` 并为对应维度轴启用 `axisPointer`，十字/阴影样式需避免遮挡读数。
- [x] **步骤 3：接入 reset action。**工具栏“重置缩放”通过 Canvas ref 只把 dataZoom 恢复到全范围；不调用 `restore` 重置用户视图配置，不清理参考线或用户选区。
- [x] **步骤 4：保持窗口状态规则明确。**纯标注或图例变更保留当前缩放窗口；来源结果、图表类型、维度或排序/Top N 发生变化时重置缩放，避免旧窗口索引指向新数据。若 ECharts option 更新无法保留窗口，显式捕获并恢复有效的 start/end，而非重建实例后静默回到默认状态。
- [x] **步骤 5：验收交互。**执行 Task 2 的目标 Node 测试；在桌面浏览器用 100+ 分类 fixture 验证滑块、滚轮/触控板缩放、跨系列对齐和一键复位；用手机尺寸检查触摸缩放与按钮可达性。

**Task 2 浏览器记录：**临时固定 fixture 含 120 分类；桌面验证 slider 从全范围缩到 55%，滚轮缩到 4%–95%，重置回 0%–100%，点击高密度折线映射到“区域 040”对应来源行 40；390×844 视口下滑块拖动、复位按钮可达。当前自动化输入接口不提供真实触屏手势，故手机 pinch 未验证。

**完成条件：**线/柱图可缩放且各指标按同一维度对齐；复位只恢复窗口；短分类图不被多余 slider 挤压；饼图无不适用的轴交互。

---

## Task 3：参考标注与范围可追溯的统计

**文件：**

- 新建 `askdb-web/lib/chart-statistics.ts`
- 修改 `askdb-web/lib/chat-output.ts`
- 修改 `askdb-web/lib/chart-output.ts`
- 视精确平均值实现需要修改 `askdb-web/lib/chart-decimal.ts`
- 修改 `askdb-web/components/assistant-ui/elements/chart-result.tsx`
- 修改 `askdb-web/lib/local-thread-adapter.tsx`（仅当通用 override/undo 无法保存新字段时）
- 新建 `askdb-web/tests/chart-statistics.test.mjs`
- 修改 `askdb-web/tests/chart-output.test.mjs`、`askdb-web/tests/chat-output.test.mjs`、`askdb-web/tests/thread-result-artifacts.test.mjs`

**配置字段：**扩展 `ChartViewConfiguration` 可选 `annotations`，由白名单校验器负责规范化：

```ts
type ChartAnnotations = {
  reference_lines: Array<{
    id: string;
    metric_field: string;
    kind: "value" | "mean" | "peak";
    value?: string; // kind=value 时存十进制文本，保留原始精度
    scope: "returned_rows" | "viewport";
    label: string;
  }>;
  reference_areas: Array<{
    id: string;
    source_row_indices: number[];
    label: string;
  }>;
};
```

新旧视图都规范化为 `annotations: { reference_lines: [], reference_areas: [] }`，写入 override 时 schema 设为 4。

`reference_areas.source_row_indices` 是用户明确保存为重点区间的 brush 选区，索引始终指向绑定的 `source_result_id` 原始 rows；最多保存 1,000 个有效索引。临时 brush 选区只留在组件内存。峰值点和均值线保存统计类型及 scope，按绑定结果即时重算，不持久化旧结果的计算值。

- [x] **步骤 1：写统计纯函数测试。**覆盖正负值、十进制字符串、null/非法值、并列峰值、不同指标、无有效值、返回行范围和 viewport 范围；结果包括 `value`、`validCount`、`scope`。运行目标 Node 测试确认新增用例失败。
- [x] **步骤 2：实现精确统计。**对每个指标分别读取原始结果值；均值以十进制精度计算，峰值用精确比较，不使用传给 ECharts 的浮点几何值。`returned_rows` 使用全部已返回行；`viewport` 使用当前排序/Top N 投影与当前 dataZoom 窗口的交集。列出有效样本数。
- [x] **步骤 3：扩展配置兼容。**`createRecommendedChartView` 给新字段安全空默认值；`validateChartView` 限制 metric field 必须属于当前可见数值指标，kind/scope 为固定枚举，value 为有限十进制文本，label 长度不超过 80，最多 8 条 reference lines；reference area 只接收绑定结果范围内的整数索引，去重后最多 1,000 个。更新 `chartViewsEqual`、变更摘要、override reader 和 undo；schema 1–3 继续可读，新保存使用 schema 4。
- [x] **步骤 4：渲染标注。**阈值/目标值用 `markLine`；每个指标峰值用 `markPoint`；已保存的行区间用 `markArea`。保存时记录 `source_row_indices`，渲染时按当前排序/Top N 投影找回位置，并把相邻展示位置合并为区间，不能仅按可能重复的分类标签定位。label 使用现有字段名与单位格式化，并逐指标显示。只允许 line/bar；pie 隐藏轴标注控件并保留切片点击。
- [x] **步骤 5：实现控件和范围文案。**用户可添加/删除 target、threshold、mean、peak；可在“已返回结果”和“当前展示窗口”间选择统计 scope。brush 选区显示“保存为重点区间”动作，保存后写入 `reference_areas` 和现有 undo 历史。tooltip/统计条显示“均值/峰值、有效样本数、统计范围”。`possibly_incomplete` 显示“本查询结果可能不完整；统计仅基于已返回数据”；`unknown` 显示“数据完整性未明确”；`not_marked_truncated` 显示“查询端未标记截断”。三种状态都不得出现“全量/总体”措辞。
- [x] **步骤 6：验证配置生命周期。**运行 `node --experimental-strip-types --test askdb-web/tests/chart-statistics.test.mjs askdb-web/tests/chart-output.test.mjs askdb-web/tests/chat-output.test.mjs askdb-web/tests/thread-result-artifacts.test.mjs`。覆盖旧 override 回放、添加/撤销标线以及同一来源结果校验。

**完成条件：**标注不能引用不存在或非数值字段；mean/peak 值与 scope 可追溯；可能截断时警示常驻；配置刷新和历史回放不丢标注，撤销只撤销最近一次图表配置编辑。

---

## Task 4：图表图片和查询结果 CSV 导出

**文件：**

- 新建 `askdb-web/lib/chart-export.ts`
- 修改 `askdb-web/components/assistant-ui/elements/chart-canvas.tsx`
- 修改 `askdb-web/components/assistant-ui/elements/chart-result.tsx`
- 新建 `askdb-web/tests/chart-export.test.mjs`

- [x] **步骤 1：定义并测试 CSV。**纯函数输入 `columns`、`columnTypes`、原始 `rows` 和 completeness 状态，输出含 UTF-8 BOM 的 CSV。测试逗号、引号、换行、Unicode、null、十进制字符串、危险公式前缀和重复列名。
- [x] **步骤 2：实现安全 CSV。**使用原始列和值，不用排序后的展示行、不应用 Top N、不仅导出聊天 markdown 可见的 20 行。字符串单元格以正确 CSV quoting 编码；以 `= + - @` 等公式起始符开头的文本做 spreadsheet-safe 中和。Arrow 类型为数字的负数/十进制文本保持数值，不被误加文本前缀。若列名重复且对象型行无法区分同名列，禁用导出并说明数据契约无法无损还原，不生成重复错值。
- [x] **步骤 3：标明导出范围。**CSV 按当前 `queryArtifact.rows.length` 导出已返回行；按钮附近和文件名显示 N 行及三态完整性：`truncated=true` 或计数不匹配显示“结果可能不完整”，明确 `truncated=false` 显示“本次结果未标记截断”，缺少完整性证据显示“完整性未明确”。不能把 rowCount 描述为数据库总行数或全库数据总数。
- [x] **步骤 4：实现 PNG。**通过 Canvas 实例 `getDataURL`/等价导出生成当前图表视觉快照；使用 app 生成的 ASCII 安全文件名，不直接使用自由输入标题。处理图表实例不存在、渲染失败和下载拒绝，提供简短可访问反馈。
- [x] **步骤 5：验证并清理资源。**运行 `node --experimental-strip-types --test askdb-web/tests/chart-export.test.mjs`。确认 Blob URL 在下载后释放；CSV fixture 覆盖公式防护；PNG 导出不发网络请求且不含 SQL/内部调试信息。

**完成条件：**PNG 可从当前图表下载；CSV 行列和值与 artifact 一致；行数与可能截断状态在下载前清楚可见；公式文本不会作为表格公式执行。

---

## Task 5：点选、框选、行详情与安全追问

**文件：**

- 修改 `askdb-web/components/assistant-ui/elements/chart-canvas.tsx`
- 修改 `askdb-web/components/assistant-ui/elements/chart-result.tsx`
- 修改 `askdb-web/lib/chart-interactions.ts`
- 修改 `askdb-web/tests/chart-interactions.test.mjs`

- [x] **步骤 1：写事件映射测试。**验证折线/纵横柱状图点击映射到正确原始行；重复类别仍按原始行区分；brush 多系列去重合并行 ID；过期 `source_result_id`、非法 dataIndex 和超出上限选区被拒绝。
- [x] **步骤 2：实现点选详情。**series 点击后以 `sourceRowIndex` 查当前 `queryArtifact.rows`，在可访问的详情 Dialog/Sheet 展示该查询结果行的列名和值。明确标题为“查询结果行”，不把聚合结果误称为数据库明细记录。键盘可访问方式与鼠标点击等效。
- [x] **步骤 3：实现 brush。**增加明确的“框选”模式开关；进入后显示状态并暂时把拖拽用于框选，退出/按 Escape 后恢复正常缩放和悬停。折线图与纵柱按 x 维度选连续范围，横柱按 y 维度选连续范围；`brushSelected` 映射到来源行索引并去重。高密度折线即使默认隐藏符号，也必须能准确选中最近的真实点/轴类别，不能用未验证的近似行替代。默认追问上下文最多 50 行、每格最多 256 个字符；超限时要求缩小范围，不静默截去中间行。饼图只支持 slice click。
- [x] **步骤 4：实现选区生命周期。**显示已选行数与维度范围；新选区替换旧选区，清除或复位按钮可清空临时选区。用户显式点击“保存为重点区间”才把来源行索引写入 `ChartViewOverride`；未保存选区只存在组件内存。切换 thread、source result、chart dimension 或影响行投影的 view 后失效并清除临时选区。选区不得写入 URL、localStorage 独立副本、日志或模型隐藏字段。
- [x] **步骤 5：接入用户可见追问。**“追问选中数据”只填充现有聊天 composer 中用户可见、可编辑的自然语言上下文（来源图表维度/已选范围/完整性状态），不得发送隐藏 SQL。用户检查后显式点击聊天发送，才进入现有 `/v1/chat` 链路；Agent 查询仍经现有 SQL 验证与 Wren `dry_plan → dry_run → query`。
- [x] **步骤 6：限制上下文与输出。**追问仅携带图表维度和可见指标字段的选中值，不携带整份结果、SQL、隐藏字段或工具 trace。文本按用户输入处理并限制长度；显示所选结果“来自本轮已返回行，结果可能不完整”提示。组件状态清理和 turn completion 不得影响其他图表或线程。
- [x] **步骤 7：验证聊天发送边界。**运行 `node --experimental-strip-types --test askdb-web/tests/chart-interactions.test.mjs askdb-web/tests/chart-output.test.mjs`。用 mock composer 确认选区动作本身不调用 `send`；只有用户检查并主动点击发送才提交追问。

**完成条件：**点选显示正确来源行；框选只引用当前结果行；所有追问内容在发送前可见；选区本身不产生 Agent/API/SQL 请求；需要新查询时仅走现有聊天查询安全链路和明确的用户确认。

---

## Task 6：集成验收与交付说明

**文件：**

- 修改 `askdb-web/README.md`（仅当其当前图表能力说明因本次实现变得过期时）
- 本计划列出的测试文件

- [x] **步骤 1：运行定向 Web Node 测试。**

```bash
node --experimental-strip-types --test \
  askdb-web/tests/chart-output.test.mjs \
  askdb-web/tests/chart-interactions.test.mjs \
  askdb-web/tests/chart-statistics.test.mjs \
  askdb-web/tests/chart-export.test.mjs \
  askdb-web/tests/chat-output.test.mjs \
  askdb-web/tests/thread-result-artifacts.test.mjs \
```

- [ ] **步骤 2：执行浏览器手动验收。**用 mock/fixed fixture 检查：短/长折线和柱状图、横向柱状图、饼图、无值和重复类别、完整/可能截断结果、zoom reset、mean/peak 范围标签、历史回放/undo、CSV 打开内容、PNG 下载、点选/brush 详情、确认发送及取消发送。
- [x] **步骤 3：检查类型与差异。**按 `askdb-web/AGENTS.md` 先查 `askdb-web/node_modules/next/dist/docs/` 中与实际修改 API 有关的 Next 指南，再运行与本次 Web 改动相关的格式/类型检查。执行 `git diff --check`，分别审阅已暂存、本次未暂存和新增文件；确认没有敏感 YAML、凭证、真实数据、构建产物或无关变更被加入本次修改。
- [x] **步骤 4：报告验证事实。**逐项报告 Node 测试、类型检查、格式检查和浏览器手动验收结果；未运行的项标成未运行，已有失败先对照本次变更判断是否由本次引入。未经授权不 stage/commit/push/创建 PR。

**Task 6 浏览器验收记录：**120 分类双系列折线 fixture 验证滑块缩放、27%–100% 窗口统计变化、重置回 0%–100% 且保留选区；PNG/CSV 按钮均报告下载成功，CSV 显示本次 120 行/3 列；点选打开“查询结果行”对话框且来源值准确；拖动框选返回来源行 27–54 共 28 行，Escape 可退出；追问动作只填入可编辑 composer，发送按钮未触发；均值、峰值和目标标注可保存，撤销可恢复，保存为重点区间后出现 28 行阴影区域。CSV 内容及短图、横柱、饼图、无效值、重复类别和完整性三态由定向 Node 测试覆盖，未逐种在浏览器 fixture 重放。键盘行选择器已在无障碍树中呈现为 combobox/list，但键盘选中动作未确认；当前 CUA 输入不提供真实 pinch，手机触控缩放未验证。

**续验记录（2026-10-06）：**扩展定向套件复跑仍为 77 项、69 通过、8 失败，失败项与 Task 0 所列基线完全相同；TypeScript、15 个改动文件的 Oxfmt 和 `git diff --check` 均通过。尝试恢复浏览器验收时，两次调用 CUA `getState()` 都在 30 秒后超时且没有返回浏览器状态，因此本轮无法补跑其余 fixture 场景，Task 6 步骤 2 保持未完成；不得把未重放项描述为浏览器通过。

**主工作区同步记录（2026-10-06）：**按用户请求，将隔离 worktree 的 15 个图表实现与测试文件复制到主工作区；逐文件 `cmp` 确认内容一致，主工作区 Oxfmt 检查通过。未暂存或提交；主工作区既有的登录、设置、品牌 UI 改动保留。

**完成条件：**四项需求可在同一查询结果图表中协同工作，历史图表仍可回放，安全与只读查询链路不变，交付报告准确区分验证通过项与未验证项。

---

## 粗略工作量与风险

- Task 1–2（稳定 Canvas、缩放和指示线）：约 1–2 人日。
- Task 3（标注持久化、精确统计和截断范围）：约 1.5–2.5 人日。
- Task 4（PNG/CSV 和导出安全）：约 0.5–1 人日。
- Task 5–6（选区追问、回归验收）：约 1.5–2.5 人日。
- 总计约 5–8 人日；本计划撰写时 HEAD `4960c7c` 已包含图表编辑能力，该自然语言修改功能现已移除。

主要风险是现有 `chart-result.tsx` 已承载较多状态，且当前 Canvas option 更新会销毁实例。实现时应将 ECharts 实例和选区交互分离，不机械拆分无关逻辑；另一个风险是查询结果以对象行表示，重复列名无法无损导出，遇到该形状必须安全禁用 CSV，而不是生成有误数据。开发工作量估算不包括不同 checkout 之间迁移既有图表改动的工作。

## 开发 Agent 可直接接收的启动指令

```text
请阅读并执行 docs/superpowers/plans/2026-10-06-chart-interactions-export-selection.md。先读取仓库 AGENTS.md 和 askdb-web/AGENTS.md，记录当前分支/worktree/git status，并确认 checkout 是否包含 HEAD 4960c7c feat: enhance chart editing and conversation workflows。按 Task 0–6 顺序实现；保护已有修改，只改本计划范围；查询图表选区不得直接触发 Agent/API/SQL，统计和导出必须明确范围与完整性状态。按计划运行定向 Node 测试、类型/格式检查和 mock fixture 浏览器验收，并逐项报告验证结果。未经用户明确要求，不要暂存、提交、推送、创建 PR 或合并。
```

## ECharts 参考

- [Event and Action：click 参数、dataIndex 与 datazoom 事件](https://echarts.apache.org/handbook/en/concepts/event/)
- [Apache ECharts Features：dataZoom、brush 等交互组件](https://echarts.apache.org/en/feature.html)
- [ECharts Area 示例：dataZoom、restore、saveAsImage](https://echarts.apache.org/examples/en/editor.html?c=area-simple)
- [ECharts 安全指南：下载文件名和不可信输入](https://echarts.apache.org/handbook/en/best-practices/security/)
