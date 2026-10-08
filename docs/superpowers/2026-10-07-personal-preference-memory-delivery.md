# 个人偏好记忆交付说明

日期：2026-10-07。实际工作区 `/Users/xuyonghua/Documents/ASKDB-Agent`，main 基线 `833314a`。按开发提示词执行，由主 Agent 直接实施。现有聊天适配器、thread UI、开发文档和业务记忆提示修改予以保留；没有提交、推送、部署、重启用户服务或执行真实业务 SQL。

## 已实现的调用链

个人设置（默认关闭，管理员/成员各自独立）→ 同源 BFF → 已认证 Principal → PostgreSQL。聊天明确保存、更新、忘记独立于 Query Gate；纯管理在查询 runtime lease 前处理。混合请求先使用开始快照查询，成功分析描述来自实际工具执行，之后提交保存；写入 epoch 在关闭后阻止旧任务重新落库。

- A 表达：语言、称呼、回答组织进入回答 Agent 的本轮输出规则；语言和称呼也用于澄清 Gate 与安全兜底，当前明确要求优先；普通聊天不自动保存。
- B 展示：图表默认类型接入本轮 chart request，数据形状校验照常执行；元/千元/万元/亿元只按 MDL 显式源单位转换表格和图表展示，不改变原始行。
- C 操作习惯：相关步骤进入本轮 Agent；简单问题不自动扩展为完整分析。
- D 常用范围：结构化默认条件不受可选 top-k 丢弃，重新绑定当前源字段并核对 SQL；采用的条件在反馈中显示。
- E 个人口径：当前 MDL 定义参与解释，直接聚合投影与字段必须可确认；无法绑定先澄清，不改共享 MDL。
- F 分析方法：保存受限步骤、条件、相对时间规则和展示描述；刚才的方法只引用真实成功分析元数据，后续按当前日期和新查询结果执行。

权限、当前 Wren revision/digest、上下文预算和 `validate_read_query → dry_plan → dry_run → query` 保持原入口约束。无法确定的个人范围不会静默绕过；可明确发送“本轮不用个人记忆，……”继续，只影响本轮。

## 主要文件

| 部分 | 文件 |
|---|---|
| 增量迁移 | `askdb-agent/src/integrations/migrations/003_personal_preferences.sql`、`askdb-agent/src/integrations/migrations/004_personal_preference_turn_metadata.sql` |
| 三类模型、独立默认与版本密文 | `src/model_settings.py`、`src/application/model_settings.py`、`src/integrations/model_services.py`；Web 模型页面及 model API/BFF |
| 权威记录、删除屏障与确认 | `src/domain/personal_memory.py`、`src/integrations/personal_memory_store.py`、现有 `deletion_journal.py` |
| 云检索/重建 | `src/integrations/personal_memory_index.py`、`src/application/personal_memory.py` |
| 本轮执行/展示 | `src/application/personal_memory_query.py`、`personal_memory_display.py`、`chat.py`、`src/tools/wren_query.py`、Web `lib/chat-output.ts` |
| API/轮次/历史 | `src/api/routes/personal_memory.py`、`routes/chat.py`、`personal_memory_stream.py`、`chat_stream.py`、conversation store/domain |
| 设置与反馈 | Web `app/settings/preferences`、`app/api/me/[...path]`、`components/settings/personal-preferences-page.tsx`、个人记忆 notice、chat adapter/history adapter |

`src/` 在上表指 Agent 的 `askdb-agent/src/`。详细实施简化写入同日技术设计的“实施对齐说明”。

## 如何配置和启用

1. 使用现有 PostgreSQL 部署，安装可用的 pgvector 扩展。增量迁移器首次连接自动按序应用 003、004；已有 002 会检查/创建 vector 扩展，需要已有部署数据库角色具备相应权限。保留现有 Fernet 加密密钥及独立删除日志；备份恢复时一并保留删除日志，否则不能兑现删除屏障。
2. 用户自行按既有启动方式重启 Agent/Web，使新代码和迁移生效；本次没有重启用户服务。没有增加 Python/npm 依赖，沿用 psycopg、httpx2、SQLGlot、LangChain 与当前 Web 组件。
3. 管理员在 `/settings/models` 配置 Chat、Embedding、Rerank。每类分别设默认，另选“记忆处理 Chat 模型”，该引用不随会话 Chat 切换。Chat 填原有 Base URL、Key、上下文/输出预算和 tokenizer。非 Chat 填完整接口 URL、Key、协议及维度/候选数；模型测试按钮发真实探测请求，由用户执行。
4. Embedding 示例 `text-embedding-v4`、1024 维、`openai_embedding`；完整地址使用本人地域/业务空间的 `/embeddings` 接口。Rerank 示例 `qwen3-rerank`、`compatible_rerank`、20 候选，使用完整 `/reranks` 接口。DashScope 原生协议分别使用不同的输入/输出结构，必须选择对应协议，不按模型名字猜测。当前阿里云地址以官方控制台和文档为准：[Embedding](https://www.alibabacloud.com/help/en/model-studio/embedding)、[Text Rerank](https://www.alibabacloud.com/help/en/model-studio/text-rerank-api)。Key 只保存在服务端密文中。
5. 每位用户打开 `/settings/preferences` 的个人开关，再在聊天明确说“记住……”；数据库提交成功才反馈“已记住”。管理员模型页查看活动/构建代际、维度、待处理和待重试数。Embedding 未就绪可按结构化记录和 BM25 使用；重建完成后自动切换，旧查询向量使用旧版本配置。
6. 金额展示需要当前 Wren MDL 的 `models[].columns[].properties.unit` 明确写 `元`、`千元`、`万元` 或 `亿元`。没有声明或投影是复合表达式时保留原显示，不猜币种或比例。

## 验证

- 独立临时 PostgreSQL 18 + pgvector，使用虚构记录及假模型返回，不访问真实业务库/云 API：个人隔离、动作幂等、关闭/重开 epoch、独立删除日志恢复、确认一次消费、模型类型和版本密文、索引切换和迟到任务、明确动作在轮次预留前不写入、SQL 条件检查。
- 受影响的既有模型设置、只读查询及 owner-scoped turn 测试同时执行。最终关键检查 29 项通过；94 个 Python 源文件 AST 解析通过；Web `tsc --noEmit --incremental false`、定向 oxfmt 检查和 `git diff --check` 通过。定向 oxlint 无错误，既有 local-thread-adapter 有 7 个未使用函数警告（HEAD 中已存在）。
- 曾额外运行既有 `test_thread_api_uses_server_memory_and_owner_scoped_history`，失败于其调用 `GET /v1/threads` 缺少当前必填 `view`，返回 422 后测试访问 `threads`。HEAD 的该参数同样必填，未为了这项无关旧测试修改 API 契约。
- 云服务连通性、模型实际提取/匹配质量、浏览器 UI 和真实查询 E2E 未验证；不存在“真实业务验收已通过”的结论。临时测试服务会在交付前关闭。

## 手动验收清单

1. 账号 A 开启记忆，保存“记住，称呼我小陈，中文先结论后依据”；新会话验证 A 表达。说“这次简短一点”后列表不增加。
2. B：“记住金额用万元，分析优先柱状图”。使用已声明金额源单位的数据源，核对表格/图表显示和原始值；换未知单位字段应保留原单位；本次明确折线图优先。
3. C：“记住分析销售时先看总额，再按需要看趋势”。简单“销售额多少”不额外查询趋势；明确“分析销售”按当前问题采用步骤。
4. D：保存可绑定字段的“默认只看华东地区”；下一轮看范围反馈和 SQL；明确“这次看全部地区”覆盖默认；不存在/多义字段必须先询问。
5. E：“记住我的销售额按实付金额求和”。用具有明确实付定义的 MDL 验证聚合和反馈；无法映射或需不支持公式时澄清，共享 MDL 不改变。
6. F：先完成分析，再“记住刚才的方法，下次分析近 30 天销售也这样”。确认只保存实际成功步骤；新会话按新日期/数据执行；失败查询后请求记住方法应提示未保存。测试明确日期保持固定。
7. 更新同场景偏好应告知替换；不同场景并存。明确忘记一条直接处理，模糊忘记提供可辨识候选；清空只确认一次；刷新历史后已消费/过期/状态变化按钮失效。
8. 页面编辑内容、源范围、期限和删除；失败保留草稿；到期停止召回。删除来源会话后个人记忆仍在。
9. 关闭时当前回答继续原快照，下一轮停用；关闭/重开期间的在途保存不能落库；关闭状态明确忘记仍可执行。
10. 切换用户 B（含管理员）不能看到/使用 A 记忆；账号切换清掉旧页面状态。切换会话 Chat 不改变专用记忆模型；Embedding 改维度观察重建，不能混用空间。
11. 断开 Embedding/Rerank，检查降级提示；专用 Chat 或个人存储不可用时显示错误，含必要范围不能静默扩大；显式“本轮不用个人记忆”不绕过其他权限/安全约束。

## 2026-10-07 应用库迁移校正

- 本机 `askdb-agent/.env` 的 PostgreSQL service 指向 `askdb_agent_dev`。迁移台账显示该库已应用 `003_personal_preferences`，但原文件后来追加的 `analysis_descriptor`、`personal_events` 两条 ALTER 尚未在库中执行。
- 从当前 003 文件中移除这两条语句后，SHA-256 与库内原记录完全一致；据此恢复已应用的 003，并将两列放入新迁移 `004_personal_preference_turn_metadata.sql`。
- 项目迁移器已将该库推进到 004。只核验了迁移台账及列定义，没有读取应用表数据；未运行测试套件。

## 实际实现边界

- 有界 SQL 校验支持单层 SELECT + AND + eq/in/gte/lt 和直接聚合；OR、NOT、嵌套和复杂指标先澄清。没有通用 SQL 等价证明器。
- 表达/操作步骤是经过选择的本轮输入，反馈不会声称模型一定遵循；范围、指标和展示的“已采用”由实际查询检查/展示配置生成。
- 管理快照限 500 项，列表按 owner 有界分页；类型筛选对已加载项生效，可继续加载。确认仅通过服务端 request_id/choice_id；普通“是”不会自行删除。
- 云密钥轮换不会改变向量空间；清除凭据撤销历史密钥，旧代际不可调用时退回关键词/结构化记录。索引为 owner+当前状态+授权源先约束后的精确 pgvector 排序，不建立 ANN 后过滤链。
