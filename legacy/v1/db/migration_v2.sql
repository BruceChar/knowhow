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

-- ============ eval_report：评测历史持久化（v2 黄金集起） ============
CREATE TABLE IF NOT EXISTS eval_report (
    id              BIGSERIAL PRIMARY KEY,
    goldset_name    TEXT NOT NULL,
    goldset_version TEXT,
    backend         TEXT NOT NULL DEFAULT 'local',
    recall_at_5     REAL,
    precision_at_5  REAL,
    mrr_at_5        REAL,
    fact_accuracy   REAL,
    red_line_errors INT DEFAULT 0,
    gate_passed     BOOLEAN,
    total_cases     INT,
    hit_cases       INT,
    detail_json     JSONB NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_eval_report_created ON eval_report (created_at DESC);

-- ============ 账户登录：users + sessions ============
CREATE TABLE IF NOT EXISTS users (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('agent','editor','reviewer','admin')),
    password_hash TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sessions (
    token       TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL,
    last_seen   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id);

-- ============ 认证加固 v3：强制改密标志 ============
ALTER TABLE users ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false;

-- ============ RBAC v4：system_ 前缀系统表（账户/角色/权限树） ============
-- 设计：账户 system_account → 角色 system_role → 权限点 system_permission（模块→页面→功能树）
-- 默认账户：admin/admin（全权限）、editor/editor（业务员）；内置角色 4 个作权限模板，企业可自建角色
CREATE TABLE IF NOT EXISTS system_account (
    id                  SERIAL PRIMARY KEY,
    username            TEXT UNIQUE NOT NULL,
    display_name        TEXT NOT NULL,
    password_hash       TEXT NOT NULL,
    enabled             BOOLEAN NOT NULL DEFAULT true,
    must_change_password BOOLEAN NOT NULL DEFAULT false,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS system_role (
    id          SERIAL PRIMARY KEY,
    code        TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    data_scope  TEXT NOT NULL DEFAULT 'full',   -- 'standard'=public+internal；'full'=全部
    builtin     BOOLEAN NOT NULL DEFAULT false,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS system_permission (
    id     SERIAL PRIMARY KEY,
    code   TEXT UNIQUE NOT NULL,
    name   TEXT NOT NULL,
    module TEXT NOT NULL,
    page   TEXT NOT NULL,
    sort   INT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS system_role_permission (
    role_id         INT NOT NULL REFERENCES system_role(id) ON DELETE CASCADE,
    permission_code TEXT NOT NULL REFERENCES system_permission(code) ON DELETE CASCADE,
    PRIMARY KEY (role_id, permission_code)
);
CREATE TABLE IF NOT EXISTS system_account_role (
    account_id INT NOT NULL REFERENCES system_account(id) ON DELETE CASCADE,
    role_id    INT NOT NULL REFERENCES system_role(id) ON DELETE CASCADE,
    PRIMARY KEY (account_id, role_id)
);

-- 会话改关联 system_account
DROP TABLE IF EXISTS sessions;
CREATE TABLE sessions (
    token      TEXT PRIMARY KEY,
    account_id INT NOT NULL REFERENCES system_account(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    last_seen  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sessions_account ON sessions (account_id);

-- ============ 权限点树（模块 → 页面 → 功能） ============
INSERT INTO system_permission (code, name, module, page, sort) VALUES
  ('ingest.view',     '查看批次',        '数据管理', '数据入录', 10),
  ('ingest.upload',   '上传文件',        '数据管理', '数据入录', 11),
  ('ingest.run',      '执行入录',        '数据管理', '数据入录', 12),
  ('faq.view',        '查看知识卡',      '知识管理', '知识卡管理', 20),
  ('faq.edit',        '编辑/打标',       '知识管理', '知识卡管理', 21),
  ('faq.review',      '审核发布',        '知识管理', '知识卡管理', 22),
  ('faq.deprecate',   '下线/驳回',       '知识管理', '知识卡管理', 23),
  ('suggestion.view', '查看建议单',      '知识管理', '建议单管理', 30),
  ('suggestion.submit','提交建议',       '知识管理', '建议单管理', 31),
  ('suggestion.review','处理建议',       '知识管理', '建议单管理', 32),
  ('eval.view',       '查看评测',        '质量保障', '评测总览', 40),
  ('eval.run',        '运行评测',        '质量保障', '评测总览', 41),
  ('audit.view',      '召回审计',        '质量保障', '召回审计', 42),
  ('redline.view',    '红线案例',        '质量保障', '红线案例', 43),
  ('qalog.view',      '问答日志',        '质量保障', '问答审计日志', 44),
  ('dashboard.view',  '驾驶舱总览',      '系统管理', '驾驶舱', 50),
  ('account.manage',  '账户管理',        '系统管理', '账户管理', 51),
  ('role.manage',     '角色与权限',      '系统管理', '角色与权限', 52),
  ('workspace.view',  '知识浏览',        '员工门户', '知识浏览', 60),
  ('learn.use',       '学习培训',        '员工门户', '学习路径', 61),
  ('agent.use',       '客户问答',        '客服工作台','客户问答', 70),
  ('agent.feedback',  '问答反馈',        '客服工作台','客户问答', 71)
ON CONFLICT (code) DO NOTHING;

-- ============ 内置角色（模板，不可删） ============
INSERT INTO system_role (code, name, description, data_scope, builtin) VALUES
  ('admin',    '管理员', '全部权限 + 系统管理',        'full',     true),
  ('reviewer', '审核员', '知识审核/打标/评测/审计',     'full',     true),
  ('editor',   '业务员', '知识编辑/入录/共建',          'full',     true),
  ('agent',    '客服',   '只读检索问答 + 提交建议',     'standard', true)
ON CONFLICT (code) DO NOTHING;

-- 内置角色权限模板
INSERT INTO system_role_permission (role_id, permission_code)
SELECT r.id, p.code FROM system_role r, system_permission p
WHERE r.code='admin'
ON CONFLICT DO NOTHING;

INSERT INTO system_role_permission (role_id, permission_code)
SELECT r.id, p.code FROM system_role r, system_permission p
WHERE r.code='reviewer' AND p.code IN
  ('faq.view','faq.edit','faq.review','faq.deprecate','suggestion.view','suggestion.submit','suggestion.review',
   'eval.view','eval.run','audit.view','redline.view','qalog.view','dashboard.view',
   'workspace.view','learn.use','agent.use','agent.feedback')
ON CONFLICT DO NOTHING;

INSERT INTO system_role_permission (role_id, permission_code)
SELECT r.id, p.code FROM system_role r, system_permission p
WHERE r.code='editor' AND p.code IN
  ('faq.view','faq.edit','ingest.view','ingest.upload','ingest.run',
   'suggestion.view','suggestion.submit','dashboard.view',
   'workspace.view','learn.use','agent.use','agent.feedback')
ON CONFLICT DO NOTHING;

INSERT INTO system_role_permission (role_id, permission_code)
SELECT r.id, p.code FROM system_role r, system_permission p
WHERE r.code='agent' AND p.code IN
  ('faq.view','suggestion.submit','workspace.view','agent.use','agent.feedback')
ON CONFLICT DO NOTHING;
