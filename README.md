# Knowhow

面向中小外贸企业的销售知识与 AI 工作系统。

Knowhow 不把“知识库”当成最终产品。它围绕真实客户交互完成四件事：

1. 从公司资料、标准问答和销售反馈中沉淀可复用知识。
2. 帮业务员生成有依据、符合语言和风险政策的客户回复。
3. 记录 AI 草稿、人工最终答案和知识缺口，持续改进。
4. 给老板一个可行动的工作台，而不是一堆技术指标。
5. 用团队、角色和“公司/团队/个人”可见范围隔离敏感知识。
6. 支持企业文件导入、新人训练，以及 bge-m3 + pgvector + BM25 混合检索。

V2 使用全新的数据模型和模块化单体架构。V1 源码归档在 `legacy/v1/`，仅用于历史追溯。

## 快速启动

```bash
uv sync
cp .env.example .env
docker compose up -d db
uv run python scripts/migrate.py
uv run knowhow
```

打开 <http://localhost:8000>，首次使用会进入公司初始化向导。

也可以通过安装脚本完成环境准备、迁移和服务启动：

```bash
scripts/install.sh
uv run python scripts/doctor.py
```

开发模式可跳过登录：

```dotenv
KNOWHOW_DEV_AUTH=1
```

## 当前产品流程

```text
首次初始化
  → 建立公司与负责人账户
  → 导入/创建首批知识
  → 业务员提问并获得带依据的草稿
  → 编辑、复制或标记已发送
  → 无知识/高风险问题进入知识缺口
  → 负责人补答案并发布
  → 老板工作台查看采用、缺口和知识新鲜度
```

## 项目结构

```text
src/knowhow/
├── api/          HTTP 路由与认证依赖
├── services/     业务用例和 Agent 编排
├── web/          独立 HTML、CSS、JavaScript
├── db.py         数据访问
├── domain.py     领域类型和业务规则
├── security.py   密码和会话
├── settings.py   配置
└── main.py       应用入口
db/migrations/    可顺序执行的数据库迁移
docs/             产品、架构、任务与交接文档
tests/            V2 自动化测试
legacy/v1/        已冻结的 V1 源码
```

详细设计见：

- [产品蓝图](docs/01-产品蓝图.md)
- [系统架构](docs/02-系统架构.md)
- [开发任务与进度](docs/03-开发任务与进度.md)
- [WhatsApp 渠道与询盘 CRM](docs/07-WhatsApp渠道与询盘CRM.md)

## 测试

```bash
uv run pytest -q
```

