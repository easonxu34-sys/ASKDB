# 图表编辑意图精简交付与验证记录

## 已实现

- `domain/chart_edit_operations.py` 定义仅包含操作列表、查询操作和澄清码的模型协议。保留全部现有能力；小数位作为独立操作，避免重填格式配置。
- `application/chart_edit_operations.py` 将短操作确定性转换成现有内部 `ChartEditIntent`。同目标冲突整体澄清；未提及的属性不生成补丁。
- `ChartEditInterpreter` 使用新 schema，发送程序派生的能力提示，只在结构化解析/协议校验失败时纠错一次。纠错仅附安全码，不附模型原始响应和 provider 异常文本；provider 失败、超时和取消不走纠错。
- `normalizeChartViewChange` 统一手动和自然语言编辑的类型、维度、颜色、格式默认值、失效排序和 Top N 清理。柱状图/折线图之间保留兼容颜色；切换饼图不自动改数据标签显隐。
- 自然语言展示编辑进入现有 Dialog 预览，取消不保存。确认应用前再次核对源结果/消息/线程/基础配置，成功后通过原提交函数保存视图及撤销历史。
- 混合查询仍经原确认消息、聊天授权、安全查询和同轮结果关联链路执行。没有新增 SQL 执行入口、SSE 事件或持久化版本。

## 已运行验证

| 验证 | 结果 |
| --- | --- |
| Agent 操作协议、解释器、业务策略和 API 测试 | 160 passed |
| Web 编辑流程、历史、请求校验、图表编译及缓存测试 | 67 passed |
| 六组 Python 编译结果 → 现有 BFF/Web `readChartEditIntent` | 全部通过 |
| Web `tsc --noEmit --incremental false` | 通过；修改前基线也通过 |
| 受影响 Web 文件 `oxlint` | 0 error，3 个对象 spread 空 fallback 样式警告 |
| `git diff --check` | 通过 |

回归覆盖操作字段约束、所有格式模式、小数位独立编辑、冲突去重、一次安全纠错、provider 不支持、取消、能力上下文边界、原 API 授权/lease 清理、配置输入不变、排序/Top N 保留和清理、两种编辑入口一致性、本地提案只预览、过期上下文拒绝及原缓存/撤销链路。

## 已有失败项

`chart-edit-api-route.test.mjs` 的五项测试中四项通过，一项失败：无效成功响应时实现返回 `AGENT_RESPONSE_INVALID`，测试期待 `CHART_EDIT_OUTPUT_INVALID`。该测试直接依赖的 BFF 路由、`agent-proxy.ts` 和 `chart-edit-request.mjs` 本次均未改动；路由的既有返回分支与断言不一致，单独运行同样失败。本轮保留该行为和测试，没有为使测试变绿改写外部错误码。

## 未验证

- 新 `ChartEditModelIntent` schema 的真实 provider/model 兼容性和自然语言理解准确率。旧兼容性报告针对 Probe / 旧 schema，不能作为新 schema 验证。
- 浏览器端到端的对话框布局、自然语言提案取消/应用和真实缓存回放；离线流程测试与类型检查不能替代这些验证。
- 没有执行真实数据查询、部署、提交、推送或 PR 创建。

## 工作区边界

在用户指定的当前工作区增量完成，保留原有改动；对本次主要修改文件保存了修改前快照以便逐文件核对。新增设计和计划均为中文，并更新旧设计的立即保存表述及旧模型兼容性报告的适用范围。
