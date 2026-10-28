-- Productization v3: production snapshots, customer usability and versions.

ALTER TABLE faq
  ADD COLUMN IF NOT EXISTS customer_ready BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE faq
  ADD COLUMN IF NOT EXISTS published_snapshot JSONB;
ALTER TABLE faq
  ADD COLUMN IF NOT EXISTS published_embedding vector(1024);
ALTER TABLE faq
  ADD COLUMN IF NOT EXISTS published_version INT;

CREATE TABLE IF NOT EXISTS faq_version (
  id            BIGSERIAL PRIMARY KEY,
  faq_id        BIGINT NOT NULL REFERENCES faq(id) ON DELETE CASCADE,
  version       INT NOT NULL,
  snapshot      JSONB NOT NULL,
  status        TEXT NOT NULL CHECK (status IN ('approved','superseded')),
  created_by    TEXT,
  approved_by   TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  approved_at   TIMESTAMPTZ,
  UNIQUE (faq_id, version)
);

CREATE INDEX IF NOT EXISTS idx_faq_version_item
  ON faq_version (faq_id, id DESC);

UPDATE faq
SET
  published_snapshot = jsonb_build_object(
    'question', question,
    'answer', answer,
    'category', category,
    'tags', tags,
    'sources', sources,
    'risk_level', risk_level,
    'action_level', action_level,
    'permission', permission,
    'customer_ready', customer_ready,
    'valid_from', valid_from,
    'valid_until', valid_until
  ),
  published_embedding = embedding,
  published_version = version
WHERE status = 'approved' AND published_snapshot IS NULL;

CREATE OR REPLACE VIEW active_faq AS
SELECT
  f.id,
  COALESCE(f.published_snapshot->>'question', f.question) AS question,
  COALESCE(f.published_snapshot->>'answer', f.answer) AS answer,
  COALESCE(f.published_snapshot->>'category', f.category) AS category,
  COALESCE(ARRAY(SELECT jsonb_array_elements_text(f.published_snapshot->'tags')), f.tags) AS tags,
  COALESCE(ARRAY(SELECT jsonb_array_elements_text(f.published_snapshot->'sources')), f.sources) AS sources,
  COALESCE(f.published_snapshot->>'risk_level', f.risk_level) AS risk_level,
  COALESCE(f.published_snapshot->>'action_level', f.action_level) AS action_level,
  COALESCE(f.published_snapshot->>'permission', f.permission) AS permission,
  COALESCE((f.published_snapshot->>'customer_ready')::boolean, f.customer_ready) AS customer_ready,
  COALESCE((f.published_snapshot->>'valid_from')::date, f.valid_from) AS valid_from,
  COALESCE((f.published_snapshot->>'valid_until')::date, f.valid_until) AS valid_until,
  COALESCE(f.published_embedding, f.embedding) AS embedding,
  COALESCE(f.published_version, f.version) AS version
FROM faq f
WHERE f.published_snapshot IS NOT NULL;

ALTER TABLE eval_report ADD COLUMN IF NOT EXISTS mode TEXT NOT NULL DEFAULT 'dev';
CREATE INDEX IF NOT EXISTS idx_faq_customer_ready_published
  ON faq (customer_ready, status);

CREATE TABLE IF NOT EXISTS mapping_config_version (
  id            BIGSERIAL PRIMARY KEY,
  config_key    TEXT NOT NULL,
  version       INT NOT NULL,
  config        JSONB NOT NULL,
  description   TEXT,
  created_by    TEXT,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (config_key, version)
);

INSERT INTO mapping_config_version
  (config_key, version, config, description, created_by)
SELECT config_key, version, config, description, updated_by
FROM mapping_config
ON CONFLICT (config_key, version) DO NOTHING;

ALTER TABLE import_batch
  ADD COLUMN IF NOT EXISTS mapping_version_id BIGINT
  REFERENCES mapping_config_version(id) ON DELETE SET NULL;

ALTER TABLE offer
  ADD COLUMN IF NOT EXISTS ext_json JSONB NOT NULL DEFAULT '{}'::jsonb;

ALTER TABLE offer DROP CONSTRAINT IF EXISTS offer_status_check;
ALTER TABLE offer
  ADD CONSTRAINT offer_status_check
  CHECK (status IN ('draft','approved','expired','rejected'));
