# AGENT.md

## 项目方向

当前运行的是 Knowhow V2，源码位于 `src/knowhow/`。`legacy/v1/` 仅供历史参考，
不得被新应用导入，也不要在旧结构上继续扩展。

开始工作前阅读：

1. `docs/01-产品蓝图.md`
2. `docs/02-系统架构.md`
3. `docs/03-开发任务与进度.md`

## 产品约束

- 价值发生在客户交互现场，优先完善销售工作台和真实业务流程。
- 事实知识、回答规则和 AI 生成严格分离，不得让模型编造价格、交期或认证承诺。
- 每条知识必须有来源、风险级别和发布状态。
- 所有客户回复必须记录 AI 草稿、人工最终答案、发送状态和修改差异。
- 老板首页展示可执行事项和业务指标，不堆砌技术指标。
- 前端不得使用浏览器原生 `alert`、`confirm`、`prompt`，统一使用自定义表单卡片。

## 工程约束

- 保持模块化单体，不提前拆微服务。
- 路由只负责认证、参数校验和调用服务，业务逻辑放在 `services/`。
- 不得修改已经执行过的数据库迁移；数据库变化新增
  `db/migrations/NNN_description.sql`。
- 一客户一数据库部署模型，新增数据必须带 `organization_id`。
- 页面使用独立 HTML/CSS/JavaScript 资产，不恢复巨型内联脚本。

## 每次更新流程

每次修改仓库内容都必须：

1. 运行测试和静态检查：

   ```bash
   PYTHONPATH=src .venv/bin/python -m pytest -q
   PYTHONPATH=src .venv/bin/python -m compileall -q src scripts tests
   node --check src/knowhow/web/assets/app.js
   git diff --check
   ```

2. 更新 `docs/03-开发任务与进度.md` 的状态、验证结果和剩余问题。
3. 按功能使用 Conventional Commits 提交。
4. 每次更新都 push 到 `origin`。
