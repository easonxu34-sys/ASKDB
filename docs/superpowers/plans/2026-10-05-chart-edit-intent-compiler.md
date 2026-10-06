# 图表编辑意图精简与统一编译实施计划

**目标：** 保留全部现有编辑能力，精简模型输出并统一配置编译和预览确认。

**架构：** 模型输出短操作对象，程序转换成现有内部意图。Web 共用纯配置归一化函数，继续使用实际结果校验和现有提交/历史缓存。

**设计：** `docs/superpowers/specs/2026-10-05-chart-edit-intent-compiler-design.md`。

**约束：** 当前工作区保留全部已有改动；主 Agent 顺序执行；不提交、不推送、不部署、不访问真实数据；不读取敏感 YAML。

## 1. 精简模型协议

- [x] 先增加 `tests/test_chart_edit_operations.py`：短操作转换、所有旧能力、冲突/未知/空对象拒绝、未提及排序不生成。
- [x] 运行测试确认新协议尚未实现，随后新增 `domain/chart_edit_operations.py` 和 `application/chart_edit_operations.py`。
- [x] 内部 `ChartEditIntent` 和 HTTP 响应保持兼容；模型协议不接受旧 patch。

## 2. 共用配置归一化

- [x] 在 `tests/chart-edit-flow.test.mjs` 增加类型切换、兼容颜色保留、排序/Top N 清理及输入不变测试，先确认失败。
- [x] 从 `lib/chat-output.ts` 的现有应用函数抽取 `normalizeChartViewChange`；手动 updateDraft 与自然语言编译共同调用。
- [x] 手动组件中删除被共用函数接管的重复兼容性规则。

## 3. 能力上下文与一次纠错

- [x] 更新 `tests/test_chart_edit_interpreter.py` 的模型 fixture 为新操作对象，测试 schema、能力、安全纠错和调用上限。
- [x] `ChartEditInterpreter` 改用短协议，能力上下文由校验后的字段/视图派生。
- [x] 只在结构化解析/协议校验失败时附安全原因码重试一次；不添加弱格式降级。
- [x] 运行解释器、application 和 API 授权/lease 回归测试。

## 4. 统一预览确认

- [x] `chart-edit-flow.mjs` 的 apply 分支改为 preview handler；测试不会调用 commit/query。
- [x] `chart-result.tsx` 将成功提案载入现有 Dialog 草稿，记录 before 视图和源上下文，取消丢弃。
- [x] 用户应用时检查上下文仍匹配，成功保存来源为 natural_language；保存失败保留原视图。
- [x] 保持混合查询确认、新结果校验、缓存隔离和撤销行为。

## 验证命令

```sh
askdb-agent/.venv/bin/python -m pytest askdb-agent/tests/test_chart_edit_operations.py askdb-agent/tests/test_chart_edit.py askdb-agent/tests/test_chart_edit_interpreter.py askdb-agent/tests/test_chart_edit_api.py -q
node --experimental-strip-types --test askdb-web/tests/chart-edit-*.test.mjs askdb-web/tests/chart-output.test.mjs askdb-web/tests/chat-output.test.mjs askdb-web/tests/thread-result-artifacts.test.mjs
git diff --check
```

单独运行 Web 类型检查，区分基线错误和新增错误。真实模型/浏览器端到端未验证时明确记录，不以离线测试替代。

实施及离线回归完成。验证结果、既有 BFF 错误码断言失败项和真实模型/浏览器未验证边界见 `docs/superpowers/reviews/2026-10-05-chart-edit-intent-compiler.md`。
