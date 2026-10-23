-- ============ 产品化演进 · 增量迁移 V2 ============
-- 新增：原始录入层 + 映射配置 + 审计/回流扩展
-- 幂等：IF NOT EXISTS，可重复执行

-- ---------- 5. 原始录入层 ----------
-- 导入批次：一次上传/导入的元信息
CREATE TABLE IF NOT EXISTS import_batch (
  id            BIGSERIAL PRIMARY KEY,
  source_file   TEXT,                        -- 原始文件名
  file_sha256   TEXT,                        -- 文件指纹（幂等去重）
  file_type     TEXT,                        -- xlsx / docx / json / pdf
  source_kind   TEXT NOT NULL                -- facts / faq / manual / image_meta
                CHECK (source_kind IN ('facts','faq','manual','image_meta')),
  mapping_id    BIGINT,                      -- 用的哪份映射配置
  total_rows    INT NOT NULL DEFAULT 0,
  loaded_rows   INT NOT NULL DEFAULT 0,
  pending_rows  INT NOT NULL DEFAULT 0,
  bad_rows      INT NOT NULL DEFAULT 0,
  status        TEXT NOT NULL DEFAULT 'running'
                CHECK (status IN ('running','done','failed','rolled_back')),
  error_msg     TEXT,
  started_by    TEXT,
  started_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at   TIMESTAMPTZ
);

-- 原始数据留档：入录前的"原样数据"，逐行可追溯
CREATE TABLE IF NOT EXISTS raw_data (
  id            BIGSERIAL PRIMARY KEY,
  batch_id      BIGINT NOT NULL REFERENCES import_batch(id) ON DELETE CASCADE,
  sheet_name    TEXT,                        -- 源 sheet / 模块名
  row_no        INT,                         -- 源文件行号（可定位）
  raw           JSONB NOT NULL,              -- 原样内容（未映射前）
  mapped        JSONB DEFAULT '{}'::jsonb,   -- 映射后（入库前快照）
  verdict       TEXT NOT NULL DEFAULT 'pending'
                CHECK (verdict IN ('pending','loaded','pending_manual','bad')),
  errs          TEXT[] DEFAULT '{}',         -- 校验错误（bad 时填）
  target_kind   TEXT,                        -- facts / faq
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 6. 映射配置（配置即数据） ----------
CREATE TABLE IF NOT EXISTS mapping_config (
  id            BIGSERIAL PRIMARY KEY,
  config_key    TEXT UNIQUE NOT NULL,        -- jiacong_facts / jiacong_faq / ...
  config        JSONB NOT NULL,              -- {mapping:{}, spec_cols:[], required:[]}
  description   TEXT,
  version       INT NOT NULL DEFAULT 1,
  active        BOOLEAN NOT NULL DEFAULT true,
  updated_by    TEXT,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ---------- 7. 审计/回流扩展 ----------
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS golden_hit    BOOLEAN;  -- 该查询是否命中黄金集
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS eval_metrics  JSONB;    -- 评测指标快照
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS user_feedback TEXT;     -- 客服/用户反馈（回流入口）
ALTER TABLE audit_log ADD COLUMN IF NOT EXISTS issue_id      BIGINT;   -- 关联 issue

ALTER TABLE issue ADD COLUMN IF NOT EXISTS priority      TEXT NOT NULL DEFAULT 'normal'
                CHECK (priority IN ('low','normal','high','critical'));
ALTER TABLE issue ADD COLUMN IF NOT EXISTS resolved_by   TEXT;
ALTER TABLE issue ADD COLUMN IF NOT EXISTS resolved_at   TIMESTAMPTZ;
ALTER TABLE issue ADD COLUMN IF NOT EXISTS related_audit BIGINT;       -- 关联审计记录

-- ---------- 索引 ----------
CREATE INDEX IF NOT EXISTS idx_raw_batch   ON raw_data (batch_id);
CREATE INDEX IF NOT EXISTS idx_raw_verdict ON raw_data (verdict);
CREATE INDEX IF NOT EXISTS idx_batch_status ON import_batch (status, source_kind);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_log (created_at);
CREATE INDEX IF NOT EXISTS idx_issue_status ON issue (status, priority);
