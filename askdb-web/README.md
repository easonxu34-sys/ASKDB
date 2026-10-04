# askdb-web

基于 Next.js、React 和 assistant-ui 的 AskDB 聊天界面，通过同源 `/api/chat` 将 SSE 请求转发到 Python Agent。

## 本地启动

需要 Node.js。当前开发环境没有安装 pnpm，因此可以直接使用 npm 安装并运行：

```bash
npm install
npm run dev
```

开发服务器默认地址为 `http://localhost:3000`。先启动 `askdb-agent`，再打开此页面；浏览器只请求同源 Next.js API，不直接连接 Python 服务或 Wren。Agent 地址可通过本机 `.env.local` 中的 `ASKDB_AGENT_URL` 覆盖。

## 账号登录与会话选择

## 聊天图表

助手可将本轮成功查询结果渲染为折线图、柱状图或饼图。明确请求类型可使用“折线图”/`line chart`、“柱状图”/`bar chart` 或“饼图”/`pie chart`；没有指定时，时间维度优先生成折线图，其他分类维度生成柱状图。饼图只支持一个数值指标和最多 8 个分类。图表由已保存的查询结果 artifact 恢复，不会在历史回放时重新查询；无效或不匹配的图表 artifact 会被跳过，查询表格仍可用。

## 账号登录与会话选择

- 用户从 `/login` 登录；首位管理员由 Agent 主机上的 `uv run askdb-agent auth init-admin` 命令一次性初始化，普通账号由管理员创建。
- Agent 是身份、角色、会话与数据源授权的权威方。Web 通过同源 BFF 使用 HttpOnly 会话 Cookie、Origin 和 CSRF 校验；浏览器不接触 Agent session token，也不直接请求 Python Agent。
- `/api/settings/models` 及 Wren 数据源管理只供管理员使用；普通用户聊天时只会收到安全的模型选项和当前用户已获准的数据源。
- 模型选择、thread metadata 和消息保存在浏览器按稳定 `user_id` 分区的 `localStorage`；新 thread ID 也包含用户命名空间。旧匿名 storage key 保留但登录后不读取，旧服务端 thread 不会自动认领。
- 生产部署必须启用 HTTPS，并将 Web 与 Agent 限制在可信部署网络；本机 loopback 开发环境可使用 HTTP。
