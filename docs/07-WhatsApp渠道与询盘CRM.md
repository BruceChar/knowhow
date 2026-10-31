# WhatsApp 渠道与询盘 CRM

> 2026-11-01 · 产品决策与待办

## 1. 结论

Knowhow 应建设官方 WhatsApp 渠道，并把消息直接接入轻量询盘 CRM。

目标不是增加一个通用聊天窗口，而是让客户对话进入以下业务闭环：

```text
客户消息
→ 客户与询盘识别
→ 产品事实和历史承诺检查
→ AI 草稿
→ 业务员确认
→ WhatsApp 发送
→ 送达、已读和客户回复
→ 报价、样品、成交或丢单
→ 知识缺口和新人训练
```

渠道本身不是壁垒。壁垒来自 Knowhow 掌握的客户级事实、承诺历史、
人工修改、发送结果和成交结果。即使以后由 WorkBuddy、Accio Work 或
其他 Agent 承担部分交互，Knowhow 仍应是可信事实和客户承诺的权威来源。

## 2. 产品定位

正式定位：

> WhatsApp 原生进入的外贸询盘、报价与承诺工作台。

第一版不替代完整 CRM/ERP，只管理：

- 客户与联系人
- 询盘和会话
- 产品事实与客户承诺
- 报价、样品、成交和丢单结果
- 分配、待回复和逾期

客户已有 CRM 时，Knowhow 负责同步、补全和校验，不要求客户整体迁移。

## 3. 渠道边界

只接官方 WhatsApp Business Platform，不通过 WhatsApp Web 自动化、
个人号机器人或其他非官方方式接入。

已核对的官方约束：

- Cloud API 通过 webhook 接收消息及送达/已读状态。
- Embedded Signup 可让客户授权自己的 WhatsApp 账户和号码。
- 已有 WhatsApp Business App 的客户可通过 Coexistence 保留原应用和号码，
  在客户授权后同步最近 180 天单聊历史和联系人。
- Coexistence 不同步群聊；部分广播、通话和商业工具能力受限。
- 客户发起消息后打开 24 小时客服窗口；窗口关闭后只能发送获批模板。
- 发送必须基于用户授权，模板按营销、工具、认证和服务分类计费。
- 2026-10-01 起，服务消息按送达的发出消息计费；每个业务号码每月有
  1000 条免费服务消息额度，超出后按市场收费。
- WhatsApp 对 AI Provider 和一般用途 AI 助手有独立政策，Knowhow 应保持
  垂直销售助手、人工确认和可追责的产品边界。
- 国内网络可达性、跨境数据处理和客户消息保留周期必须在部署前实测并
  形成书面方案。

## 4. 架构原则

```text
WhatsApp Cloud API / BSP
          │
          ▼
Webhook Ingress：验签、快速响应、原始事件留档
          │
          ▼
异步处理：去重、号码路由、媒体下载、状态更新
          │
          ▼
Conversation Service
          │
          ├── Contact / Inquiry / Message
          ├── Product Fact / Commitment / Approval
          ├── AI Draft / Shared Inbox
          └── Outcome / Gap / Training
```

工程要求：

- 业务代码依赖统一 `ChannelProvider`，不直接依赖 BSP 专有格式。
- 早期允许 BSP/Cloud API 双适配；长期以 Cloud API + Embedded Signup 为主。
- 以 WhatsApp `wamid` 做幂等，原始 webhook 事件可回溯和重放。
- 每个企业的访问 token 加密存储，按 `organization_id` 隔离。
- Webhook 立即返回，耗时处理进入异步队列。
- 发送前检查客服窗口、模板状态、用户授权和高风险事实。
- 消息发送与报价承诺快照必须原子关联。
- 客户拥有号码、WhatsApp 账户和业务数据，可解除绑定并导出。

## 5. 核心数据对象

| 对象 | 责任 |
|---|---|
| ChannelConnection | 企业、WAAC/Messaging account、号码、授权状态和 token |
| ContactIdentity | WhatsApp 用户、电话号码、所属客户 |
| Conversation | 会话归属、负责人、待回复/SLA/结案状态 |
| Message | 入站、出站、媒体、模板、送达、已读和失败 |
| Inquiry | 产品、数量、目的地、贸易条款和客户需求 |
| Commitment | 客户级价格、MOQ、交期、认证、有效期和审批人 |
| Outcome | 报价、样品、成交、丢单和成交金额 |
| KnowledgeGap | 无答案、冲突、过期和大改草稿形成的工作项 |

`Interaction` 保留 AI 草稿和人工最终答案，并关联到具体
`Conversation`、`Message` 和 `Commitment`。

## 6. 分阶段待办

### P3.0：付费试点闭环

- [ ] 选定一个细分客户群和真实 WhatsApp 询盘场景。
- [ ] 确认试点客户拥有 WhatsApp Business App 号码、Meta 企业账户管理权限，
      并同意同步必要的历史单聊记录。
- [ ] 配置 Meta 开发者应用、业务验证、App Review、权限和回调域名。
- [ ] 实现 Cloud API 或 BSP 的首个渠道适配器。
- [ ] 实现 Coexistence/Embedded Signup 接入现有号码。
- [ ] 实现 webhook 验签、幂等、原始事件留档、送达/已读状态。
- [ ] 实现联系人、会话、询盘和消息最小数据模型。
- [ ] 实现团队共享收件箱、分配、待回复和逾期提示。
- [ ] 将客户问题接入现有产品事实、知识检索和 AI 草稿。
- [ ] 人工确认后从 Knowhow 发送 WhatsApp 文本和附件。
- [ ] 记录最终答案、发送状态、客户回复和成交/丢单结果。
- [ ] 从高频问题、无答案和大改草稿自动形成知识缺口。
- [ ] 形成试点 ROI 报告：回复时间、草稿采用、承诺错误和成交周期。

### P3.1：可复制产品化

- [ ] Embedded Signup 自助接入和一键解除绑定。
- [ ] 模板创建、审核状态、模板变量和 24 小时窗口守卫。
- [ ] 客户侧 WhatsApp 费用账单、用量和透传对账。
- [ ] 多号码、多团队和跨时区路由。
- [ ] 外部 CRM/ERP 双向同步。
- [ ] MCP/API 输出受控事实、承诺校验和结果回写能力。
- [ ] WhatsApp 指标：送达、已读、回复、模板拒绝、质量和限额。
- [ ] 部署监控：token 失效、账户断开、webhook 堆积和发送失败。
- [ ] 数据保留、删除、导出、审计和跨境合规方案。

## 7. 试点验收

- [ ] 至少一个真实号码连续运行四周。
- [ ] 询盘从进入到最终发送不需要手工复制粘贴。
- [ ] 100% 客户报价可追溯到已发布事实、人工决定和发送消息。
- [ ] 价格、交期和认证红线事实错误为 0。
- [ ] 草稿采用或少量修改后采用率达到 60%。
- [ ] 首次回复准备时间相对基线下降 30%。
- [ ] Top 50 高频问题中 80% 有已发布答案。
- [ ] 至少两个同行业付费试点愿意续费或扩大范围。

## 8. 商业边界

- WhatsApp 消息费透明透传，不以补贴消息费作为增长手段。
- 核心订阅按团队、渠道和受控事实源收费，不只看席位。
- 首期数据治理、历史同步、规则配置和模板建设可单独收费。
- 私有化部署是高价值版本，不是唯一交付方式。
- 不用“自动回复率”作为核心指标，优先衡量正确承诺、节省时间和成交结果。

## 9. 官方资料

- [WhatsApp Cloud API Get Started](https://developers.facebook.com/documentation/business-messaging/whatsapp/get-started)
- [Embedded Signup](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/overview)
- [WhatsApp Business App Coexistence](https://developers.facebook.com/documentation/business-messaging/whatsapp/embedded-signup/onboarding-business-app-users)
- [WhatsApp Business Platform Pricing](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing)
- [AI Providers Pricing Policy](https://developers.facebook.com/documentation/business-messaging/whatsapp/pricing/ai-providers)
