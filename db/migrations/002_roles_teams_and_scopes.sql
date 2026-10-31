CREATE TABLE IF NOT EXISTS access_role (
  id                BIGSERIAL PRIMARY KEY,
  organization_id   BIGINT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
  code              TEXT NOT NULL,
  name              TEXT NOT NULL,
  builtin           BOOLEAN NOT NULL DEFAULT false,
  permissions       TEXT[] NOT NULL DEFAULT '{}',
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (organization_id, code)
);

CREATE TABLE IF NOT EXISTS team (
  id                BIGSERIAL PRIMARY KEY,
  organization_id   BIGINT NOT NULL REFERENCES organization(id) ON DELETE CASCADE,
  name              TEXT NOT NULL,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (organization_id, name)
);

ALTER TABLE account
  ADD COLUMN IF NOT EXISTS role_id BIGINT REFERENCES access_role(id)
  ON DELETE RESTRICT;

CREATE TABLE IF NOT EXISTS team_member (
  team_id           BIGINT NOT NULL REFERENCES team(id) ON DELETE CASCADE,
  account_id        BIGINT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (team_id, account_id)
);

ALTER TABLE knowledge_unit
  ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'organization'
  CHECK (scope IN ('organization','team','private'));
ALTER TABLE knowledge_unit
  ADD COLUMN IF NOT EXISTS team_id BIGINT REFERENCES team(id) ON DELETE SET NULL;

ALTER TABLE evidence
  ADD COLUMN IF NOT EXISTS scope TEXT NOT NULL DEFAULT 'organization'
  CHECK (scope IN ('organization','team','private'));
ALTER TABLE evidence
  ADD COLUMN IF NOT EXISTS team_id BIGINT REFERENCES team(id) ON DELETE SET NULL;

INSERT INTO access_role
  (organization_id,code,name,builtin,permissions)
SELECT o.id, defaults.code, defaults.name, true, defaults.permissions
FROM organization o
CROSS JOIN (VALUES
  ('owner','老板',ARRAY[
    'ask.use','knowledge.view','knowledge.suggest','knowledge.edit',
    'knowledge.publish','gap.manage','dashboard.view','team.manage',
    'role.manage','data.all'
  ]),
  ('admin','管理员',ARRAY[
    'ask.use','knowledge.view','knowledge.suggest','knowledge.edit',
    'knowledge.publish','gap.manage','dashboard.view','team.manage',
    'role.manage','data.all'
  ]),
  ('steward','知识负责人',ARRAY[
    'ask.use','knowledge.view','knowledge.suggest','knowledge.edit',
    'knowledge.publish','gap.manage','dashboard.view','data.all'
  ]),
  ('sales','业务员',ARRAY[
    'ask.use','knowledge.view','knowledge.suggest'
  ])
) AS defaults(code,name,permissions)
ON CONFLICT (organization_id, code) DO NOTHING;

UPDATE account a
SET role_id=r.id
FROM access_role r
WHERE a.role_id IS NULL
  AND r.organization_id=a.organization_id
  AND r.code=a.role;

INSERT INTO team (organization_id,name)
SELECT o.id,'销售部'
FROM organization o
WHERE NOT EXISTS (
  SELECT 1 FROM team t WHERE t.organization_id=o.id AND t.name='销售部'
);

INSERT INTO team_member (team_id,account_id)
SELECT t.id,a.id
FROM account a
JOIN team t ON t.organization_id=a.organization_id AND t.name='销售部'
ON CONFLICT DO NOTHING;

CREATE INDEX IF NOT EXISTS idx_access_role_org
  ON access_role (organization_id, code);
CREATE INDEX IF NOT EXISTS idx_team_member_account
  ON team_member (account_id, team_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_scope
  ON knowledge_unit (organization_id, scope, team_id);

