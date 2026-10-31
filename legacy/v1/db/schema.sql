-- ============ 扩展与公共 ============
CREATE EXTENSION IF NOT EXISTS vector;

-- ============ 1. 产品事实层 ============
CREATE TABLE sku (
  id            BIGSERIAL PRIMARY KEY,
  sku_code      TEXT UNIQUE NOT NULL,      -- 图鉴货号
  category      TEXT NOT NULL,             -- 19 品类之一
  name_zh       TEXT,
  name_en       TEXT,
  spec          JSONB DEFAULT '{}'::jsonb, -- 规格参数
  status        TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','approved','deprecated')),
  valid_from    DATE,
  valid_until   DATE,
  permission    TEXT NOT NULL DEFAULT 'internal'
                CHECK (permission IN ('public','internal','confidential')),
  risk_level    TEXT NOT NULL DEFAULT 'low'
                CHECK (risk_level IN ('low','medium','high')),
  version       INT NOT NULL DEFAULT 1,
  approved_by   TEXT,
  approved_at   TIMESTAMPTZ,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  embedding     vector(1024)               -- bge-m3，第5章写入
);

CREATE TABLE variant (                     -- 颜色/尺寸/包装变体
  id            BIGSERIAL PRIMARY KEY,
  sku_id        BIGINT NOT NULL REFERENCES sku(id) ON DELETE CASCADE,
  variant_code  TEXT NOT NULL,
  name_zh       TEXT,
  name_en       TEXT,
  attributes    JSONB DEFAULT '{}'::jsonb,
  embedding     vector(1024),
  UNIQUE (sku_id, variant_code)
);

CREATE TABLE offer (                       -- 已审批报价快照（实时价走接口）
  id            BIGSERIAL PRIMARY KEY,
  variant_id    BIGINT NOT NULL REFERENCES variant(id) ON DELETE CASCADE,
  currency      TEXT NOT NULL DEFAULT 'USD',
  unit_price    NUMERIC(12,4) NOT NULL,
  moq           INT,
  lead_time_days INT,
  freight_mode  TEXT,
  valid_until   DATE NOT NULL,             -- 报价有效期（15天制）
  status        TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','approved','expired')),
  approved_by   TEXT, approved_at TIMESTAMPTZ,
  version       INT NOT NULL DEFAULT 1,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============ 2. 知识层 ============
CREATE TABLE faq (                         -- 知识卡主体（话术库/手册条目）
  id            BIGSERIAL PRIMARY KEY,
  question      TEXT NOT NULL,
  answer        TEXT NOT NULL,
  category      TEXT,                      -- 话术模块（35模块）
  tags          TEXT[] DEFAULT '{}',
  sources       TEXT[] DEFAULT '{}',       -- 证据编号列表，如 E-0012
  status        TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft','approved','deprecated')),
  valid_from    DATE,
  valid_until   DATE,
  permission    TEXT NOT NULL DEFAULT 'internal'
                CHECK (permission IN ('public','internal','confidential')),
  risk_level    TEXT NOT NULL DEFAULT 'low'
                CHECK (risk_level IN ('low','medium','high')),
  action_level  TEXT NOT NULL DEFAULT 'B'
                CHECK (action_level IN ('A','B','C','D')),
  customer_ready BOOLEAN NOT NULL DEFAULT false,
  version       INT NOT NULL DEFAULT 1,
  approved_by   TEXT, approved_at TIMESTAMPTZ,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  embedding     vector(1024),
  published_snapshot JSONB,
  published_embedding vector(1024),
  published_version INT
);

CREATE TABLE evidence (                    -- 证据原始文件（可溯源）
  id            BIGSERIAL PRIMARY KEY,
  ref           TEXT UNIQUE NOT NULL,      -- E-0001 编号
  title         TEXT,
  file_path     TEXT,
  file_type     TEXT,
  sha256        TEXT,                      -- 防篡改指纹
  uploaded_by   TEXT,
  uploaded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============ 3. 回流层 ============
CREATE TABLE issue (                       -- 客服/业务员的修改回流
  id            BIGSERIAL PRIMARY KEY,
  faq_id        BIGINT REFERENCES faq(id),
  inquiry_id    TEXT,                      -- 关联原始询盘
  feedback      TEXT,                      -- 为什么不对 / 应该怎么说
  resolution    TEXT,                      -- 处理结论
  status        TEXT NOT NULL DEFAULT 'open'
                CHECK (status IN ('open','resolved','rejected')),
  created_by    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============ 4. 审计表 ============
CREATE TABLE audit_log (
  id            BIGSERIAL PRIMARY KEY,
  inquiry_id    TEXT NOT NULL,
  channel       TEXT,
  question      TEXT,
  answer        TEXT,
  citations     JSONB,
  action_level  TEXT,
  decision      TEXT,                      -- auto / draft / escalate / block
  latency_ms    INT,
  model         TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============ 索引（第6章检索依赖） ============
CREATE INDEX idx_sku_embedding ON sku USING hnsw (embedding vector_cosine_ops);
CREATE INDEX idx_faq_embedding ON faq USING hnsw (embedding vector_cosine_ops);
CREATE UNIQUE INDEX uq_faq_question ON faq (question);  -- 幂等 upsert 依赖
CREATE INDEX idx_faq_fts ON faq USING gin (to_tsvector('simple', question || ' ' || answer));
CREATE INDEX idx_faq_status ON faq (status, valid_from, valid_until);
CREATE INDEX idx_offer_valid ON offer (valid_until, status);
