# 个人记忆查询契约实施计划

依据：`../specs/2026-10-07-personal-memory-architecture-review.md`。用户已授权按建议执行；在当前工作区直接实施，保留已有改动，不提交、不部署、不新增子 Agent 或 worktree。

- [x] 1. 类别 schema 与本轮不可变解释：为 filters/metric/time_rule/display/steps 定义有界结构，按 kind 校验允许键；读取兼容旧 display.language，隔离不兼容 payload。新建 `domain/turn_interpretation.py`，定义来源、runtime 绑定、问题项、三种投影，实际执行记录独立保存。
- [x] 2. 记忆解析与统一决策：抽取 `application/personal_memory_resolution.py`；匹配模型只提交选择、覆盖证据、冲突候选，不接受自由澄清文本。绑定依据当前 MDL，问题项由代码产出；普通查询不设置提前 reply。当前明确要求覆盖须引用当前文本并落到同一约束槽位；必要默认筛选不能因模型漏选而消失。
- [x] 3. 全链路消费：Gate 只收到查询语义投影；Agent 收到同一语义及规范展示/步骤投影；工具校验 source/revision/digest 与已绑定必要条件。移除动态字典约束，逐轮解释不可变，执行结果独立。单一全局约束不能表达的复合方法明确返回 UNSUPPORTED_CONSTRAINT。展示故障仅降级展示，必要语义故障阻止宽查询；预算优先计入必要语义，展示预算独立裁剪。
- [x] 4. 契约验证、日志与文档：验证表达展示变化时 Gate 输入字节一致，模型越界结果、冲突/未绑定/版本漂移、漏条件执行、正常成功、异常与取消释放。更新既有技术设计和架构评审状态，记录静态验证及未执行的云模型/真实数据/浏览器验收。

测试驱动验证示例：

```python
assert normalize_payload('display', {'language': '中文'})['language'] == '中文'
# 新写入不得越过类别能力。
with pytest.raises(ValueError):
    MemoryInput(kind='display', content='中文', payload={'filters': []})
# 本轮展示不能影响查询投影。
assert chinese_turn.gate_projection() == empty_turn.gate_projection()
# 无效范围不能以 READY 绕过。
assert decide_with_issues('READY', unresolved_turn).status == 'CLARIFY'
```

每阶段先补窄测试并观察失败，再实现、运行相关测试。验证命令基于 `askdb-agent/.venv/bin/python -m pytest`，不配置或连接真实业务数据库；PostgreSQL 集成测试只在明确提供 `ASKDB_TEST_DATABASE_DSN` 时运行，否则报告跳过。最后运行受影响文件 compileall、`git diff --check`，核对本轮改动与开工快照。

模块职责：domain 管类型与不变量；resolution 管读取选择绑定；personal_memory 管管理动作；chat 管统一决策；query 工具管执行约束；HTTP/SSE 保持既有契约。


## 验证结果

- 相关聊天、诊断、查询、图表、runtime 和个人偏好测试：71 passed，6 skipped（未配置 ASKDB_TEST_DATABASE_DSN）。新增契约测试包含默认和场景展示的输入隔离、旧数据规范化、未绑定与冲突、模型越界、执行版本与条件校验、写入再验证、预算和异常/取消释放。
- 扩大到 runtime_manager 时发现一项未修改代码的基线失败：test_runtime_key_includes_source_and_both_revisions 期望四段 key，当前 RuntimeSnapshot.key 为六段。该模块和旧测试不在本轮修改范围；未为通过测试改动现有版本契约。
- compileall 与 git diff --check 通过。环境未安装 ruff，未执行 lint/独立类型检查；未配置测试数据库，未运行 PostgreSQL 集成验证。
- 没有调用云模型、执行真实业务查询、进行浏览器验收、提交或部署。用户复测：保留“用中文说明”，分别开关记忆并新建会话，同一问题的 gate_input 指纹应一致；开启时 personal_resolution 显示展示投影，query_decision 和原查询审查继续执行。
