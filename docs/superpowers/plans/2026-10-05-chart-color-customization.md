# 图表颜色自定义实施计划

## 目标

在图表编辑器中支持多套内置色板、指标纯色与渐变、透明度、饼图分类纯色，并让配置在图表渲染、撤销历史和自然语言编辑上下文中一致生效。旧图表保持现有颜色外观。

## 架构决策

- 前端视图配置保存 `color_palette_id` 与有界的 `ChartColorSpec`；颜色只接受 `#RRGGBB`，透明度使用 0–100 的整数。`system_default` 保留 ECharts 系列默认色和现有饼图默认色，`classic` 显式使用经典色板。
- 指标支持纯色和线性渐变；饼图分类支持纯色。自动项跟随当前色板，自定义项保持独立。
- 继续接受旧的八种颜色 token，并在视图校验时迁移为原样式的纯色；图表 override 写入 schema v3，保留 v1/v2 读取能力。
- 自然语言编辑仍使用既有颜色 token 输出；请求侧只扩大显示上下文 schema，不扩大模型可输出的颜色能力。
- 不改变查询、数据范围、权限或 Agent 的查询授权链路。

## 实施任务

1. 在 `askdb-web/lib/chat-output.ts` 定义色板与颜色样式类型，加入严格校验、旧 token 迁移、编辑摘要和配置相等比较。
2. 在 `askdb-web/lib/chart-output.ts` 将当前色板、指标样式、饼图分类样式转换为 ECharts 配置，并保留 classic 默认色值。
3. 在 `askdb-web/lib/chart-edit-history.mjs` 与 `chat-output.ts` 将新写入升至 schema v3，并继续读取 v1/v2 override 与撤销快照。
4. 在 `askdb-web/lib/chart-edit-flow.mjs`、`chart-edit-request.mjs` 和 `askdb-agent/src/api/schemas/chart_edit.py` 校验新的视图上下文，同时让自然语言编辑 patch 继续接受旧颜色 token。
5. 在 `askdb-web/components/assistant-ui/elements/chart-result.tsx` 增加色板选择、每个指标的模式与色值/方向/透明度控件、饼图分类色值控件以及恢复自动配色操作。
6. 检查相关源码 diff、颜色验证与调用链的一致性；遵循本任务环境要求，不运行测试或构建命令。

## 全局约束

- 保留已有工作区修改，不暂存或提交文件。
- 仅修改图表编辑和颜色配置链路所需文件。
- 不读取或修改受保护的 `application.yml`。
- 不运行测试、构建或格式验证命令；交付时明确说明验证边界。
