# 图表编辑模型结构化输出兼容性验证

> 后续模型协议已改为 `ChartEditModelIntent` 短操作列表。下文远端结果只证明当时的 `Probe` / 旧 `ChartEditIntent` schema 调用；本次未重新调用真实模型，不能据此确认新操作 schema 的远端兼容性。

日期：2026-10-05

## 验证范围

检查 `integrations.models.build_model()`、`ModelSettingsApplication._probe()` 与当前已配置的可用模型 profile。普通 profile 探测只调用 `ainvoke("Reply with OK.")`，不验证结构化输出。验证使用 LangChain `with_structured_output(Probe, method="json_schema")`，仅发送一个无用户数据、无 SQL、无查询行的 `ok: true` schema 请求，并把调用上限设为 15 秒。

锁定依赖为 `langchain==1.4.3`、`langchain-openai==1.6.7`、`langchain-core==1.6.6`。当 profile 带有 `base_url` 或 API key 时，当前封装通过 `init_chat_model(..., model_provider="openai")` 创建 `ChatOpenAI`；因此 OpenAI、DeepSeek 和自定义/OpenAI-compatible profile 共用同一客户端结构化输出接口。成功构造 runnable 不代表远端 endpoint 接受 `json_schema`，必须观察实际调用。

## 当前 profile 结果

| Provider / model | 客户端构造 | 实际 `json_schema` 调用 | 结论 |
| --- | --- | --- | --- |
| `deepseek` / `deepseek-v4-flash` | `ChatOpenAI` 与结构化 runnable 构造成功 | endpoint 返回 HTTP 400 | 此组合不支持当前严格 schema 请求；图表编辑必须安全失败，不回退到 JSON mode、自由文本或函数工具调用 |
| `custom` / `mimo-v2.5` | `ChatOpenAI` 与结构化 runnable 构造成功 | 128 token 上限下返回经过 Pydantic 验证的 `Probe(ok=True)` | 本次验证支持严格 schema 调用 |
| OpenAI profile | SDK 使用相同 `ChatOpenAI` 适配器的本地构造验证通过 | 当前未配置可用的 OpenAI profile，未发送远端请求 | 远端能力未知；不能据此宣称兼容 |

`mimo-v2.5` 首次使用 24 token 的探测因输出长度限制未能完成 schema 响应；以 128 token 重新执行后成功。此次结果仅针对当时配置的具体 profile/model，不代表所有自定义 endpoint。

在 Task 1 完成后，又以 `ChartEditIntent` 的完整嵌套 schema 对 `mimo-v2.5` 发起一次最小请求；模型返回 `apply` 意图并通过完整 Pydantic 校验（最长 25 秒）。这项结果在 Task 2/3 开始前确认；DeepSeek 仍不兼容。

## 拒绝路径验证

- 已配置 DeepSeek profile 的远端调用实际返回 HTTP 400，说明仅检查 `with_structured_output()` 构造会漏掉 endpoint 的调用时拒绝。
- 对 OpenAI-compatible 客户端另用 `httpx.MockTransport` 返回模拟 HTTP 400，并确认请求携带 `response_format: json_schema`；LangChain 将其作为 `OpenAIInvalidRequestError` 抛出。它用于验证 SDK 错误路径，不替代远端兼容性结论。
- 未把 profile probe、启动时探测或任何兼容性结果写入普通 runtime readiness；普通模型 `ainvoke` 探测保持原样。

## 实施约束

解释器只从 `lease.snapshot.graph.model` 延迟构造 `with_structured_output(..., method="json_schema")`。服务端必须捕获结构化调用拒绝并返回稳定、安全错误；不得尝试自由文本解析、JSON mode 或函数工具调用作为降级。模型 profile 更换后应重新按实际调用结果处理，不以 provider 名称推断支持情况。结构化输出失败时不提交图表变更，也不启动聊天或查询。

## Node 测试运行时

仓库没有 Node 版本文件，`askdb-web/package.json` 未声明 `engines` 或测试脚本。当前验证版本为 `node v25.8.0`；`--experimental-strip-types` 可正常导入带类型标注的 `.ts` 模块。后续计划中的 `.mjs` 测试使用该 Node 版本运行。
