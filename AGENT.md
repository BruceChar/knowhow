# AGENT.md

## 会话启动

1. 先阅读本文件与 `docs/实现状态与验收记录.md`，确认当前状态、已知限制和下一验收项。
2. 检查 `git status`，保留工作区中与当前任务无关的用户改动。
3. 涉及反馈、知识发布或权限时，同时阅读 `docs/产品设计文档.md` 的相关章节。

## 不可破坏的约束

- 客服反馈经审核员采纳后，只能生成待审核知识版本；不得绕过审核直接发布。
- 标准答案与改进建议必须分流：事实内容进入 FAQ，语言/格式/澄清要求进入
  `answer_policy`；不得让 LLM 从模糊建议自动编造业务事实。
- 不得修改已经执行过的数据库迁移文件；数据库变更新增 `db/migration_vN.sql`，
  并同步 `scripts/migrate.py`。
- 反馈、权限和审计接口必须有回归测试；前端脚本修改后运行 `node --check`。

## 每次更新的固定流程

每次修改仓库内容都必须完成以下步骤，不能只留在工作区：

1. 运行与改动匹配的测试，并执行：

   ```bash
   .venv/bin/python -m pytest -q
   .venv/bin/python -m compileall -q src scripts tests
   git diff --check
   ```

2. 更新 `docs/实现状态与验收记录.md`，写清最新状态、验证结果、限制和下一步。
3. 按功能提交，使用 Conventional Commits，例如：

   ```bash
   git add -A
   git commit -m "feat(feedback): add traceable feedback loop"
   git push origin HEAD
   ```

   纯修复使用 `fix(scope): ...`，纯文档使用 `docs(scope): ...`；一个提交只包含
   一个完整功能或修复，不混入无关改动。
4. 每次更新都必须 commit 并 push 到 `origin`，不能只提交到本地。

若测试、迁移或推送失败，先修复或明确记录阻塞原因，不得把失败状态描述为完成。
