ALTER TABLE access_role
  ADD COLUMN IF NOT EXISTS team_only BOOLEAN NOT NULL DEFAULT false;

ALTER TABLE team_member
  ADD COLUMN IF NOT EXISTS role_id BIGINT
  REFERENCES access_role(id) ON DELETE RESTRICT;

INSERT INTO access_role
  (organization_id,code,name,builtin,team_only,permissions)
SELECT o.id,'lead','组长',true,true,ARRAY[
  'ask.use','knowledge.view','knowledge.suggest','knowledge.edit',
  'knowledge.publish','team.lead','training.use',
  'facts.view'
]
FROM organization o
ON CONFLICT (organization_id,code) DO UPDATE
SET team_only=true;

UPDATE team_member tm
SET role_id=COALESCE(t.default_role_id,r.id)
FROM team t
LEFT JOIN access_role r
  ON r.organization_id=t.organization_id AND r.code='sales'
WHERE tm.team_id=t.id AND tm.role_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_team_member_role
  ON team_member (team_id, role_id);

UPDATE access_role
SET permissions=array_append(permissions,'team.lead')
WHERE code='lead' AND NOT ('team.lead'=ANY(permissions));

