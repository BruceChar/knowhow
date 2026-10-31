CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS organization (
  id                BIGSERIAL PRIMARY KEY,
  name              TEXT NOT NULL,
  industry          TEXT NOT NULL DEFAULT '',
  default_language  TEXT NOT NULL DEFAULT 'zh-CN',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS account (
  id                BIGSERIAL PRIMARY KEY,
  organization_id   BIGINT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
  username          TEXT NOT NULL,
  display_name      TEXT NOT NULL,
  password_hash     TEXT NOT NULL,
  role              TEXT NOT NULL DEFAULT 'sales'
                    CHECK (role IN ('owner','steward','sales','admin')),
  enabled           BOOLEAN NOT NULL DEFAULT true,
  must_change_password BOOLEAN NOT NULL DEFAULT false,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (organization_id, username)
);

CREATE TABLE IF NOT EXISTS session (
  token_hash        TEXT PRIMARY KEY,
  account_id        BIGINT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  expires_at        TIMESTAMPTZ NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS evidence (
  id                BIGSERIAL PRIMARY KEY,
  organization_id   BIGINT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
  title             TEXT NOT NULL,
  source_type       TEXT NOT NULL DEFAULT 'manual',
  source_ref        TEXT NOT NULL DEFAULT '',
  content           TEXT NOT NULL DEFAULT '',
  sha256            TEXT NOT NULL DEFAULT '',
  created_by        BIGINT REFERENCES account(id) ON DELETE SET NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS knowledge_unit (
  id                BIGSERIAL PRIMARY KEY,
  organization_id   BIGINT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
  kind              TEXT NOT NULL DEFAULT 'faq'
                    CHECK (kind IN ('faq','product','policy','playbook','case','sop')),
  title             TEXT NOT NULL,
  question          TEXT NOT NULL DEFAULT '',
  answer            TEXT NOT NULL,
  status            TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','published','archived')),
  risk_level        TEXT NOT NULL DEFAULT 'low'
                    CHECK (risk_level IN ('low','medium','high')),
  owner_id          BIGINT REFERENCES account(id) ON DELETE SET NULL,
  evidence_id       BIGINT REFERENCES evidence(id) ON DELETE SET NULL,
  source_ref        TEXT NOT NULL DEFAULT '',
  valid_until       DATE,
  version           INT NOT NULL DEFAULT 1,
  metadata          JSONB NOT NULL DEFAULT '{}'::jsonb,
  embedding         vector(1024),
  created_by        BIGINT REFERENCES account(id) ON DELETE SET NULL,
  approved_by       BIGINT REFERENCES account(id) ON DELETE SET NULL,
  published_at      TIMESTAMPTZ,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS knowledge_version (
  id                BIGSERIAL PRIMARY KEY,
  knowledge_id      BIGINT NOT NULL REFERENCES knowledge_unit(id) ON DELETE CASCADE,
  version           INT NOT NULL,
  snapshot          JSONB NOT NULL,
  created_by        BIGINT REFERENCES account(id) ON DELETE SET NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (knowledge_id, version)
);

CREATE TABLE IF NOT EXISTS answer_rule (
  id                BIGSERIAL PRIMARY KEY,
  organization_id   BIGINT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
  name              TEXT NOT NULL,
  rule              TEXT NOT NULL,
  rule_kind         TEXT NOT NULL DEFAULT 'instruction'
                    CHECK (rule_kind IN ('instruction','language','risk','format')),
  status            TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('draft','active','archived')),
  priority          INT NOT NULL DEFAULT 100,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS interaction (
  id                BIGSERIAL PRIMARY KEY,
  organization_id   BIGINT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
  account_id        BIGINT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  question          TEXT NOT NULL,
  ai_answer         TEXT NOT NULL DEFAULT '',
  final_answer      TEXT NOT NULL DEFAULT '',
  status            TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','copied','sent','escalated')),
  channel           TEXT NOT NULL DEFAULT 'web',
  language          TEXT NOT NULL DEFAULT '',
  citations         JSONB NOT NULL DEFAULT '[]'::jsonb,
  escalated_reason  TEXT NOT NULL DEFAULT '',
  edit_distance     INT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  sent_at           TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS knowledge_gap (
  id                BIGSERIAL PRIMARY KEY,
  organization_id   BIGINT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
  interaction_id    BIGINT REFERENCES interaction(id) ON DELETE SET NULL,
  question          TEXT NOT NULL,
  reason            TEXT NOT NULL,
  status            TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open','resolved','ignored')),
  priority          INT NOT NULL DEFAULT 50,
  assignee_id       BIGINT REFERENCES account(id) ON DELETE SET NULL,
  resolution_knowledge_id BIGINT REFERENCES knowledge_unit(id) ON DELETE SET NULL,
  resolution_note   TEXT NOT NULL DEFAULT '',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at       TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_account_org ON account (organization_id, enabled);
CREATE INDEX IF NOT EXISTS idx_session_expiry ON session (expires_at);
CREATE INDEX IF NOT EXISTS idx_knowledge_org_status
  ON knowledge_unit (organization_id, status, kind);
CREATE INDEX IF NOT EXISTS idx_knowledge_question
  ON knowledge_unit USING gin (to_tsvector('simple', question || ' ' || answer));
CREATE INDEX IF NOT EXISTS idx_interaction_org_created
  ON interaction (organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_gap_org_status
  ON knowledge_gap (organization_id, status, priority DESC);

INSERT INTO answer_rule
  (organization_id, name, rule, rule_kind, status, priority)
SELECT o.id, defaults.name, defaults.rule, defaults.rule_kind, 'active', defaults.priority
FROM organization o
CROSS JOIN (VALUES
  ('客户语言一致', '客户使用中文时优先输出中文；没有中文依据时转人工。',
   'language', 10),
  ('不编造业务事实', '价格、交期、认证和赔付承诺只能来自已发布知识或人工确认。',
   'risk', 20),
  ('先确认关键信息', '产品、数量或目的地缺失且影响结论时，先向客户确认。',
   'instruction', 30)
) AS defaults(name, rule, rule_kind, priority)
WHERE NOT EXISTS (
  SELECT 1 FROM answer_rule r WHERE r.organization_id=o.id
);

