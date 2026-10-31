ALTER TABLE team
  ADD COLUMN IF NOT EXISTS default_role_id BIGINT
  REFERENCES access_role(id) ON DELETE SET NULL;

UPDATE team t
SET default_role_id=r.id
FROM access_role r
WHERE t.default_role_id IS NULL
  AND r.organization_id=t.organization_id
  AND r.code='sales';

CREATE TABLE IF NOT EXISTS account_invite (
  id                BIGSERIAL PRIMARY KEY,
  organization_id   BIGINT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
  token_hash        TEXT UNIQUE NOT NULL,
  role_id           BIGINT NOT NULL REFERENCES access_role(id) ON DELETE CASCADE,
  team_id           BIGINT NOT NULL REFERENCES team(id) ON DELETE CASCADE,
  created_by        BIGINT REFERENCES account(id) ON DELETE SET NULL,
  expires_at        TIMESTAMPTZ NOT NULL,
  used_at           TIMESTAMPTZ,
  used_by           BIGINT REFERENCES account(id) ON DELETE SET NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE interaction
  ADD COLUMN IF NOT EXISTS outcome TEXT NOT NULL DEFAULT 'unknown'
  CHECK (outcome IN ('unknown','following','won','lost'));
ALTER TABLE interaction
  ADD COLUMN IF NOT EXISTS outcome_value NUMERIC(14,2);
ALTER TABLE interaction
  ADD COLUMN IF NOT EXISTS outcome_note TEXT NOT NULL DEFAULT '';
ALTER TABLE interaction
  ADD COLUMN IF NOT EXISTS outcome_updated_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS product (
  id                BIGSERIAL PRIMARY KEY,
  organization_id   BIGINT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
  sku               TEXT NOT NULL,
  name              TEXT NOT NULL,
  category          TEXT NOT NULL DEFAULT '',
  description       TEXT NOT NULL DEFAULT '',
  status            TEXT NOT NULL DEFAULT 'draft'
                    CHECK (status IN ('draft','published','archived')),
  attributes        JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_by        BIGINT REFERENCES account(id) ON DELETE SET NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (organization_id, sku)
);

CREATE TABLE IF NOT EXISTS product_variant (
  id                BIGSERIAL PRIMARY KEY,
  organization_id   BIGINT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
  product_id        BIGINT NOT NULL REFERENCES product(id) ON DELETE CASCADE,
  code              TEXT NOT NULL,
  name              TEXT NOT NULL DEFAULT '',
  attributes        JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (product_id, code)
);

CREATE TABLE IF NOT EXISTS price_offer (
  id                BIGSERIAL PRIMARY KEY,
  organization_id   BIGINT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
  variant_id        BIGINT NOT NULL REFERENCES product_variant(id) ON DELETE CASCADE,
  currency          TEXT NOT NULL DEFAULT 'USD',
  unit_price        NUMERIC(14,4) NOT NULL,
  moq               INT,
  lead_time_days    INT,
  valid_until       DATE NOT NULL,
  status            TEXT NOT NULL DEFAULT 'published'
                    CHECK (status IN ('draft','published','expired')),
  created_by        BIGINT REFERENCES account(id) ON DELETE SET NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_team_default_role
  ON team (organization_id, default_role_id);
CREATE INDEX IF NOT EXISTS idx_invite_expiry
  ON account_invite (token_hash, expires_at);
CREATE INDEX IF NOT EXISTS idx_interaction_outcome
  ON interaction (organization_id, outcome, sent_at);
CREATE INDEX IF NOT EXISTS idx_product_org
  ON product (organization_id, status, sku);
CREATE INDEX IF NOT EXISTS idx_offer_valid
  ON price_offer (organization_id, variant_id, status, valid_until);

UPDATE access_role
SET permissions=array_append(permissions,'facts.view')
WHERE code IN ('owner','admin','steward','sales')
  AND NOT ('facts.view'=ANY(permissions));

UPDATE access_role
SET permissions=array_append(permissions,'facts.manage')
WHERE code IN ('owner','admin','steward')
  AND NOT ('facts.manage'=ANY(permissions));

