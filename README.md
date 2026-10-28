# knowhow

面向中小外贸企业的本地知识库与客服辅助系统。当前实现包含产品/FAQ
数据入录、业务人员 Import Studio、产品与报价审核、混合检索、客服辅助、
反馈回流、评测、权限和后台管理。

## Quick start

1. 复制环境变量：

   ```bash
   cp .env.example .env
   ```

2. 修改 `.env` 中的 `KNOWHOW_SECRET`、数据库密码和模型配置。

3. 启动数据库：

   ```bash
   docker compose up -d db
   ```

4. 执行数据库迁移：

   ```bash
   .venv/bin/python scripts/migrate.py
   ```

5. 启动服务：

   ```bash
   HF_HUB_OFFLINE=1 .venv/bin/uvicorn src.api.admin_api:app --host 0.0.0.0 --port 8000
   ```

6. 打开 `http://localhost:8000/agent` 进入客服辅助，或打开
   `http://localhost:8000/admin` 进入知识治理后台。

开发环境可设置 `KNOWHOW_DEV_AUTH=1` 使用 `X-User-Id` 调试；生产必须关闭。默认演示账号仅用于本地开发，生产启动时应通过 `SEED_PASSWORDS` 注入强密码并首次改密。

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

## CLI

```bash
# 产品图鉴
.venv/bin/python src/ingest/facts.py data/raw/products.xlsx \
  --config jiacong_facts

# FAQ JSON
.venv/bin/python src/ingest/faq.py data/raw/faq.json

# 评测（--dev 会检索未发布草稿，不能作为生产门禁）
.venv/bin/python src/core/eval_runner.py \
  data/goldset/jiacong_faq_goldset_v2.json --dev
```

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
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
