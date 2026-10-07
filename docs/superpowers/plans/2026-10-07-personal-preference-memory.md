# 个人偏好记忆实施进度

依据同日 requirements、design 和 development-prompt，由主 Agent 在当前 main 工作区实施，不提交、不部署、不执行真实业务查询。

- [x] 三类模型目录、各类型默认值、记忆处理 Chat 引用与消费方过滤。
- [x] 个人开关、记录、版本、幂等、写入 epoch、删除屏障和页面管理。
- [x] 专用模型解析明确动作、成功分析元数据与聊天生命周期。
- [x] pgvector 索引任务、BM25/RRF/Rerank、模型空间代际。
- [x] 本轮快照、必要范围/指标绑定、方法和展示应用。
- [x] 反馈、故障降级、静态检查、关键验证与手动验收说明。

基线：main，HEAD 833314a；已有 thread.aui.tsx、agent-chat-adapter.ts、开发文档及 memory-recall-notice 相关修改予以保留。

验证：29 项关键/已有检查通过；Python AST、Web tsc、定向 lint/format 与 diff 检查完成情况见交付说明。云服务、浏览器、真实业务查询未自动验收。
