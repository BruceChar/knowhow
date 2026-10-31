ALTER TABLE knowledge_unit
  ADD COLUMN IF NOT EXISTS embedding_model TEXT;
ALTER TABLE knowledge_unit
  ADD COLUMN IF NOT EXISTS embedding_updated_at TIMESTAMPTZ;

CREATE INDEX IF NOT EXISTS idx_knowledge_embedding_hnsw
  ON knowledge_unit USING hnsw (embedding vector_cosine_ops)
  WHERE embedding IS NOT NULL;

CREATE TABLE IF NOT EXISTS import_batch (
  id                BIGSERIAL PRIMARY KEY,
  organization_id   BIGINT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
  source_name       TEXT NOT NULL,
  source_type       TEXT NOT NULL DEFAULT 'csv',
  sha256            TEXT NOT NULL,
  status            TEXT NOT NULL DEFAULT 'preview'
                    CHECK (status IN ('preview','completed','failed')),
  mapping           JSONB NOT NULL DEFAULT '{}'::jsonb,
  preview_rows      JSONB NOT NULL DEFAULT '[]'::jsonb,
  issues            JSONB NOT NULL DEFAULT '[]'::jsonb,
  total_rows        INT NOT NULL DEFAULT 0,
  ready_rows        INT NOT NULL DEFAULT 0,
  duplicate_rows    INT NOT NULL DEFAULT 0,
  error_rows        INT NOT NULL DEFAULT 0,
  created_count     INT NOT NULL DEFAULT 0,
  created_by        BIGINT REFERENCES account(id) ON DELETE SET NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  executed_at       TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS training_attempt (
  id                BIGSERIAL PRIMARY KEY,
  organization_id   BIGINT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
  account_id        BIGINT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  knowledge_id      BIGINT NOT NULL REFERENCES knowledge_unit(id) ON DELETE CASCADE,
  response          TEXT NOT NULL,
  score             INT NOT NULL CHECK (score BETWEEN 0 AND 100),
  feedback          JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_import_batch_org
  ON import_batch (organization_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_training_attempt_account
  ON training_attempt (organization_id, account_id, created_at DESC);

UPDATE access_role
SET permissions=array_append(permissions,'training.use')
WHERE code IN ('owner','admin','steward','sales')
  AND NOT ('training.use'=ANY(permissions));

