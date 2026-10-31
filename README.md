# knowhow

面向中小外贸企业的本地知识库与客服辅助系统。当前实现包含产品/FAQ
数据入录、业务人员 Import Studio、产品与报价审核、混合检索、客服辅助、
反馈回流、评测、权限和后台管理。

## 本地快速启动（uv）

1. 安装并同步依赖：

   ```bash
   uv sync
   ```

2. 复制并检查环境变量：

   ```bash
   cp .env.example .env
   ```

   本地开发至少确认 `.env` 中的 `RAG_DSN` 与数据库一致。生产环境必须另外设置
   `KNOWHOW_SECRET`、强数据库密码和 `SEED_PASSWORDS`。

3. 启动数据库：

   ```bash
   docker compose up -d db
   ```

4. 执行数据库迁移：

   ```bash
   uv run python scripts/migrate.py
   ```

5. 启动服务（二选一）：

   ```bash
   # 方式一：项目脚本入口（推荐）
   HF_HUB_OFFLINE=1 uv run app/index

   # 方式二：等价命令 / 已注册的 console 入口
   uv run knowhow
   ```

   默认监听 `0.0.0.0:8000`（局域网可访问），可用环境变量 `APP_HOST` / `APP_PORT`
   覆盖端口。页面顶栏会自动显示当前机器的局域网访问地址（如
   `http://192.168.31.49:8000/agent`），可一键复制分享给同局域网的其他业务员。

6. 打开 `http://localhost:8000/agent` 进入客服辅助，或打开
   `http://localhost:8000/admin` 进入知识治理后台。

开发环境可设置 `KNOWHOW_DEV_AUTH=1` 使用 `X-User-Id` 调试；生产必须关闭。默认演示账号仅用于本地开发，生产启动时应通过 `SEED_PASSWORDS` 注入强密码并首次改密。

### 初始账号与密码

未设置 `KNOWHOW_PROD=1`、也没有通过 `SEED_PASSWORDS` 覆盖时，系统会创建以下
本地开发账号：

| 用户 ID | 角色 | 初始密码 |
| --- | --- | --- |
| `u_admin` | 管理员 | `admin123` |
| `u_reviewer` | 审核员 | `reviewer123` |
| `u_editor` | 业务员 | `editor123` |
| `u_agent` | 客服 | `agent123` |

当前本地管理员入口使用 `u_admin` / `admin123`。这些密码只适用于本机开发，
不得用于生产、局域网共享或内网穿透环境。生产启动时必须配置：

```dotenv
KNOWHOW_PROD=1
KNOWHOW_DEV_AUTH=0
COOKIE_SECURE=1
SEED_PASSWORDS=u_admin=足够长的强密码,u_reviewer=强密码,u_editor=强密码,u_agent=强密码
```

如果生产模式未提供 `SEED_PASSWORDS`，系统会为首次创建的种子账号随机生成密码，
并在启动日志中打印一次；应立即保存并完成首次改密。

## 当前可用闭环

1. 业务人员在 Import Studio 上传 XLSX/XLS/CSV/JSON/DOCX，选择工作表并调整字段映射。
2. 预检后只写入 draft；系统记录源文件 hash、原始行、映射结果、模板及模板版本。
3. 产品和报价分别在“产品与报价”页面审核。报价发布时会原子替换同变体的旧有效价。
4. 客服在工作台提问；系统只检索已发布、有效、权限允许且 customer_ready 的知识。
5. 价格/MOQ/交期通过已批准的 Offer 事实工具返回；没有事实或知识时升级人工。
6. 客服反馈写入审计日志；错误反馈形成待处理问题单，供后续修订知识。

当前 402 条 FAQ 和 1,458 条 SKU 仍是 draft，报价尚未正式导入，因此不能把
现有开发评测当成生产验收。完整边界和验证记录见
[`docs/实现状态与验收记录.md`](docs/实现状态与验收记录.md)。

## 使用 `uv run python` 运行命令行任务

```bash
# 产品图鉴/产品主数据
uv run python src/ingest/facts.py data/raw/products.xlsx \
  --config jiacong_facts

# FAQ JSON
uv run python src/ingest/faq.py data/raw/faq.json

# 评测（--dev 会检索未发布草稿，不能作为生产门禁）
uv run python src/core/eval_runner.py \
  data/goldset/jiacong_faq_goldset_v2.json --dev

# 检查数据库迁移状态；已执行的迁移不会重复运行
uv run python scripts/migrate.py
```

## 测试

```bash
uv run python -m unittest discover -s tests -v
```

## Architecture

- `src/api/admin_api.py`：统一 FastAPI 入口、认证、审核、评测、导入和问答。
- `src/core/`：检索、门禁、生成、评测和知识服务编排。
- `src/ingest/`：数据读取、映射、校验和导入。
- `src/providers/`：embedding、LLM 和向量存储适配器。
- `src/tools/`：受控业务事实工具。
- `db/`：基础 schema 和增量迁移。
- `config/mappings/`：示例导入模板。

详细设计见 `docs/knowhow_企业知识库产品化设计与ETL适配方案.docx` 和 `docs/产品设计文档.md`。
