# AskDB 多模型配置与会话模型选择设计

- 状态：多模型/会话选择扩展已实现并验证；Agent 测试 33 项、Web 测试 10 项、TypeScript 检查和 Next.js 生产构建均通过（2026-10-02）
- 日期：2026-10-02
- 范围：`askdb-web/` 与 `askdb-agent/` 中的多模型配置管理、会话模型选择、加密存储和运行时切换

## 1. 背景

AskDB 当前的会话侧栏由 assistant-ui thread-list 原语构成，侧栏底部展示产品标识。Agent 通过 `ASKDB_MODEL`、`OPENAI_BASE_URL` 和 `OPENAI_API_KEY` 环境变量使用一个启动时固定的模型；Agent 没有模型设置 API，且应用没有登录系统。

本功能让可信内网中的使用者通过设置入口管理多份部署级共享模型配置，并在每个会话的输入框中选择当前会话使用的模型。模型凭据在 Agent 服务端持久化；会话仅保存所选配置 ID。新聊天请求按 ID 解析已保存配置，正在执行的请求继续使用开始时捕获的模型运行时。

## 2. 已确认的决策

1. 模型配置是整个 AskDB 部署共享的配置目录，不区分用户；每个会话可独立选择其中一项。
2. 本期不加入应用登录、用户身份或多用户密钥隔离。部署必须由可信内网边界限制访问；所有能访问 AskDB 的内网用户均可修改全局模型设置。
3. 第一版只支持 OpenAI 兼容协议，包含 OpenAI、DeepSeek 和自定义兼容端点；不实现 Anthropic、Gemini 等原生 SDK 适配。
4. 模型配置保存在 Agent 本地 SQLite。每个配置的 API Key 使用 `ASKDB_SETTINGS_ENCRYPTION_KEY` 提供的 Fernet 密钥加密后存储；不把密钥写入 Web localStorage、Agent 日志、错误响应或读配置响应。
5. 每份新增或修改的模型配置保存前必须通过一次实际连通性测试。测试只发送短的探测提示，不使用用户会话消息；未通过时不保存、不更新对应 runtime。
6. 每个新会话默认使用当前默认配置；用户可在输入框模型选择栏修改该会话的选择。选择保存在该会话的浏览器本地元数据中。
7. 保存或删除配置只影响之后按对应 ID 发起的请求。已开始的请求继续使用开始时捕获的模型运行时。
8. 沿用现有 `.env` 作为首次初始化配置，并迁移为模型目录中的默认配置；SQLite 中的配置随后优先。
9. UI 复用现有 assistant-ui 会话侧栏和项目已有 Base UI Dialog；会话选择器只展示服务端配置目录，不把凭据交给 assistant-ui 模型上下文。

## 3. 用户界面

### 3.1 入口和导航

- 在左侧 Thread list sidebar 底部加入齿轮图标按钮。
- 点击后打开向上展开的轻量菜单，菜单仅含“设置模型”。
- 选择“设置模型”后打开现有 Dialog 风格的模型设置对话框。
- 沿用 AskDB 现有浅暖色背景、棕灰文字、陶土色焦点与强调色、圆角和边框；移动端对话框宽度适配视口，不遮挡键盘焦点。

### 3.2 表单

设置对话框展示模型配置目录，支持新增、编辑、设为默认和删除；新增/编辑表单沿用下表字段。

表单字段：

| 字段 | 控件与行为 |
|---|---|
| 配置名称 | 必填，用于区分同一供应商下的配置，例如“DeepSeek 日常” |
| 供应商 | 选项为 OpenAI、DeepSeek、自定义 OpenAI 兼容服务；前两者填入默认 API 地址，自定义允许编辑地址 |
| 模型名称 | 必填文本输入，例如 `deepseek-v4-flash` |
| API 地址 | 必填绝对 HTTP(S) URL；可编辑，界面不得在地址中放 API Key |
| API Key | `password` 输入框；读取设置时只显示是否已配置，绝不回填密钥；空白表示保留现有密钥 |

- 已配置的密钥可输入新值替换；另提供明确的“清除已保存密钥”操作及确认。清除后配置进入未就绪状态，聊天 API 返回稳定的模型未配置错误，直到重新设置密钥。
- 表单在打开时从同源 BFF 获取非敏感设置。加载失败时展示错误和重试入口，不显示空表单并误导为未配置。
- “测试连接”成功后启用“保存”；测试失败时保留所有输入值并显示可操作的通用错误，不回显供应商原始错误正文或密钥。provider、model、base URL 或 API Key 任一字段变更后，测试通过状态失效，需重新测试。
- 清除密钥是独立、需确认的操作，不要求先做模型连通性测试；清除成功后该配置标记为不可用于会话选择，直到重新输入密钥并测试保存。
- 默认配置不可删除；删除其他配置前无需迁移现存会话，因会话仅引用 ID。引用已删除配置的会话会回退到当前默认配置并提示用户。
- 保存失败时对话框保持打开，保留输入值。保存成功后关闭对话框并提示设置已生效。
- 加载、测试、保存时提供禁用状态和进度提示；所有按钮支持键盘操作，并为字段和状态提供可访问名称。

### 3.3 assistant-ui 组件边界

- 保留现有 `ThreadListPrimitive` 会话列表和 sidebar layout，不替换已定制的 AskDB 外观。
- 复用现有 Base UI `Dialog` 组件以及当前项目按钮、颜色和排版样式。
- 输入框旁提供轻量模型选择栏，选项来自服务端可用配置目录，显示配置名称与模型名；每个会话独立保存选择。无可用配置时禁用选择栏并给出设置入口。
- assistant-ui `ModelSelector`/`SettingsPanel` 不能存储 API 凭据、provider endpoint 或部署级设置；选择器提交的只有服务端生成的配置 ID，不能覆盖配置详情。

## 4. HTTP 契约

Web 页面只调用同源 Next.js API；Next.js BFF 转发到 Agent。Agent 只绑定本机/服务内网地址，不暴露到用户网络。生产反向代理必须仅对可信内网开放 Web 的设置和聊天入口，并为浏览器到 Web 的网络链路配置 HTTPS。

### 4.1 读取模型配置目录

- Web：`GET /api/settings/models`
- Agent：`GET /v1/settings/models`
- 返回示例：

```json
{
  "default_profile_id": "profile_abc",
  "profiles": [
    {
      "id": "profile_abc",
      "name": "DeepSeek 日常",
      "provider": "deepseek",
      "model": "deepseek-v4-flash",
      "base_url": "https://api.deepseek.com",
      "api_key_configured": true,
      "available": true
    }
  ]
}
```

返回中永远没有 API Key 或密钥密文。`available` 仅在配置完整且凭据可解密时为 true。聊天输入框只显示 `available` 配置。

### 4.2 测试模型连接

- Web：`POST /api/settings/models/test`
- Agent：`POST /v1/settings/models/test`
- 请求体使用表单中的 `name`、`provider`、`model`、`base_url` 和 `api_key`，更新现有配置时可附带 `profile_id` 以便 Agent 在空 API Key 时读取已保存密钥；生产部署的浏览器到 Web 链路必须使用 HTTPS 并通过请求体传密钥，本机开发可使用 loopback HTTP。
- Agent 使用临时模型客户端执行一次短探测（最多 16 个输出 token，15 秒超时，不携带用户消息）；不写 SQLite、不更改当前 runtime。
- 成功返回 `{"ok": true}`。失败返回稳定错误码（例如 `MODEL_AUTH_FAILED`、`MODEL_CONNECTION_FAILED`、`MODEL_REJECTED`）和通用中文说明；不得返回上游响应正文、认证头或 API Key。
- 若表单 API Key 为空但已保存密钥存在，测试和保存时均在 Agent 内使用现有密钥；密钥不从 Agent 返回给浏览器。

### 4.3 新增或更新模型配置

- Web：`POST /api/settings/models` 新增；`PUT /api/settings/models/{profile_id}` 更新
- Agent：`POST /v1/settings/models` 新增；`PUT /v1/settings/models/{profile_id}` 更新
- 请求体字段：

```json
{
  "name": "DeepSeek 日常",
  "provider": "deepseek",
  "model": "deepseek-v4-flash",
  "base_url": "https://api.deepseek.com",
  "api_key": "新密钥或空字符串"
}
```

- `provider` 只允许 `openai`、`deepseek`、`custom`；三者均通过 OpenAI 兼容适配器初始化。
- 只有更新时空白 `api_key` 表示保留当前已加密密钥。新增/更新必须先通过连通性测试才可保存；清除密钥会将已有配置转为不可用状态。
- Agent 验证字段和 URL，测试候选配置，持久化该目录项。更新只使对应 profile 的 runtime 缓存失效；其他配置不受影响。
- 成功响应与 GET 一样只返回非敏感设置及 `api_key_configured`，不回显密钥。

### 4.4 设默认和删除配置

- Web：`PUT /api/settings/models/{profile_id}/default`、`DELETE /api/settings/models/{profile_id}`
- Agent：`PUT /v1/settings/models/{profile_id}/default`、`DELETE /v1/settings/models/{profile_id}`
- 默认配置必须可用；删除当前默认配置必须在同一事务中指定另一项可用配置为新默认，否则拒绝。
- 删除后移除对应密文和 runtime 缓存。已开始的请求持有旧 runtime 并继续完成；后续引用该 ID 的请求返回 `MODEL_PROFILE_NOT_FOUND`，Web 对话将选择回退为当前默认配置并提示。

### 4.5 清除单个配置的 API Key

- Web：`DELETE /api/settings/models/{profile_id}/credential`
- Agent：`DELETE /v1/settings/models/{profile_id}/credential`
- 只清除指定配置的密文；若它是默认项，默认 ID 不变但该项不可用。聊天请求收到稳定的未就绪错误，选择器将其隐藏；重新录入密钥并测试后恢复可用。

### 4.6 会话模型选择与聊天请求

- 新会话采用目录当前 `default_profile_id`；选择栏变更时把 `model_profile_id` 随该会话元数据写入浏览器已有的本地会话存储。旧会话缺少此字段时按当前默认配置处理；用户显式选择后固定保存在该会话。
- 现有 `POST /api/chat` 与 `POST /v1/chat` 增加可选 `model_profile_id` 字段。缺省时 Agent 使用当前默认配置；存在时 Agent 必须查找该目录 ID，并只使用其服务端保存的 provider、model、base URL 和凭据。未知/已删除 ID 返回稳定错误，不静默切换到其他模型。
- Web BFF 原样转发 ID。模型名称、provider、endpoint 或密钥均不由聊天请求传入；Agent 拒绝任何尝试通过其他字段覆盖配置的请求。
- 为兼容现有调用方，已实现的单数路径 `/settings/model` 暂作为默认 profile 的兼容别名保留；新 Web 界面统一使用 `/settings/models`。兼容别名仍返回同样的非敏感数据。

## 5. Agent 存储和运行时

### 5.1 SQLite

- `model_profiles` 表保存稳定 ID、展示名称、provider、model、base URL、加密后的 API Key、创建/更新时间；单独的设置元数据保存 `default_profile_id`。
- 从当前单行配置表迁移时，将现有配置转成一个 profile 并设为默认，保留原始密文，不要求用户重新输入密钥。
- 会话所选 profile ID 保存在 Web 现有会话 metadata/localStorage 中，不写入 Agent 模型凭据表，也不把会话模型映射当作授权边界。
- 数据库路径由 `ASKDB_SETTINGS_DB_PATH` 配置，默认位于 `askdb-agent/data/model-settings.sqlite3`。生产部署应将该路径挂载到持久化卷并限制文件权限。
- 首次启动且数据库无配置时，从现有 `ASKDB_MODEL`、`OPENAI_BASE_URL`、`OPENAI_API_KEY` 初始化一个默认 profile；缺少 API Key 时保留不可用状态。
- 首次初始化的 provider 从现有 base URL 推断：DeepSeek 和 OpenAI 官方地址分别显示为 DeepSeek、OpenAI，其他地址显示为自定义端点；model 值移除现有 `openai:` provider 前缀后展示。
- 数据库配置一旦写入即优先于模型相关 `.env` 值。删除数据库不作为日常重置机制；部署操作文档说明备份/恢复数据库文件的方式。

### 5.2 API Key 加密

- `ASKDB_SETTINGS_ENCRYPTION_KEY` 是必需的 Fernet 格式密钥，应由运维生成并存入部署密钥管理系统；不得写入仓库或 SQLite。
- 若数据库从未保存过模型配置且主密钥缺失，现有环境变量模型仍可用于聊天，但设置读取、测试、保存和清除接口返回 `MODEL_SETTINGS_UNAVAILABLE`，直到设置主密钥并重启 Agent。若数据库已经有加密配置而主密钥缺失或不匹配，则 Agent 不得退回环境变量或其他模型；聊天返回配置不可用错误。
- Agent 使用 Fernet 加密 API Key，解密仅发生在 Agent 内存中并用于创建 OpenAI-compatible 模型客户端。
- 缺少主密钥、密钥格式错误或数据库密文无法解密时，设置 API 返回 `MODEL_SETTINGS_UNAVAILABLE`，并在 Agent 日志中给出不含密钥的运维错误。不得退回明文存储或静默切换到不同模型。
- v1 不提供主密钥自动轮换。轮换须在维护窗口用旧密钥解密并用新密钥重加密数据库记录；密钥丢失时需要重新录入 provider API Key。

### 5.3 Runtime 热切换

- Agent 按 profile ID 构造 LangChain OpenAI-compatible 模型客户端和 Agent graph，并按 `(profile_id, updated_at)` 缓存 runtime。请求开始时解析目录项、获取或构造 runtime 引用，并在整条 SSE 流期间保持该引用。
- Wren toolkit 对单一 Wren 项目保持固定，可在模型 runtime 重建时复用。
- Agent 使用异步锁序列化“测试/保存/初始化”涉及的存储和 runtime 切换。
- 更新或删除 profile 只影响之后开始且选择该 profile 的请求；不同 profile 的并行会话可同时使用各自模型。
- Model client 初始化不保证 API 端可达，因此“测试连接”是保存前的实际网络验证；测试成功与保存之间供应商仍可能不可用，聊天请求必须继续使用稳定、脱敏的错误事件。

## 6. 安全与失败边界

- 不增加登录意味着可信内网内的所有访问者拥有同一全局设置写权限。不能将此功能放在公网或不受信任的共享网络上；若部署边界不能保证可信，必须在部署层增加访问控制后才启用写接口。
- Web API Key 输入不持久化到浏览器，API 返回、日志、trace、错误和 SSE 均不得包含 API Key。
- 测试连接的模型输出被丢弃，不把探测内容展示给用户或记录日志。
- provider/base URL/model 仅通过设置接口修改；聊天 API 只接受服务端目录中的 `model_profile_id`，无法将任意 URL 用作模型 endpoint。
- 设置读取和写入均使用 `Cache-Control: no-store`；BFF 转发时不缓存响应。
- 输入错误返回 422；SQLite/密钥不可用返回 503；连接超时或供应商拒绝认证返回脱敏错误码。已有活动配置在更新失败时继续服务。
- Agent 日志保留请求关联信息和异常类型，不记录完整 body、API Key、认证头或上游错误正文。

## 7. 环境配置、文档和交付

- 更新 `askdb-agent/.env.example`、Agent README 和 `docs/开发文档.md`，说明 SQLite 路径、Fernet 密钥生成/配置、首次初始化规则、内网/HTTPS 部署边界及密钥备份和轮换方式。
- 将 `cryptography` 作为 Agent 的直接运行时依赖声明，不依赖 Wren 或 LangChain 间接带入该加密库。
- Web 仅代理设置 API，不保存、缓存或转录 API Key。
- 实现覆盖的接口和运行时变更应遵循当前 Agent 的 schema/route/application/integration 分层；Web BFF 设置转发与聊天 route 保持职责清晰。

## 8. 验收条件

1. 设置入口可管理多份模型配置；新增、编辑、设默认和删除行为遵循默认项及凭据约束。
2. 输入框选择栏只列出可用配置，显示名称与模型名；无可用配置时可进入设置。
3. 新会话采用当前默认配置；切换某个会话的模型后重载页面仍恢复该会话选择，其他会话不受影响。
4. 聊天请求携带 profile ID，Agent 使用对应模型；未知或已删除 ID 得到稳定错误，不被静默改派。
5. 连通性测试失败时不写入新增/更新配置；已运行的 SSE 请求在 profile 更新或删除期间完成。
6. Agent 重启后从 SQLite 恢复目录和默认项；单行旧配置迁移后原密钥仍可用。
7. GET/POST/PUT/test 响应、日志及错误内容均无 API Key；数据库中只存在密文。
8. 聊天请求无法覆盖 provider、model、base URL 或 API Key，只能引用服务端保存的 profile ID。

## 9. 参考文档

- [assistant-ui Thread list sidebar](https://www.assistant-ui.com/elements/thread-list-sidebar)
- [assistant-ui Model selector](https://www.assistant-ui.com/elements/model-selector)
- [assistant-ui Settings panel](https://www.assistant-ui.com/elements/settings-panel)
- [LangChain `init_chat_model` Python Reference](https://reference.langchain.com/python/langchain/chat_models/base/init_chat_model)
