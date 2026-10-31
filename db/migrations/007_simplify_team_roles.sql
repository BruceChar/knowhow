ALTER TABLE team_member
  ADD COLUMN IF NOT EXISTS is_leader BOOLEAN NOT NULL DEFAULT false;

UPDATE team_member tm
SET is_leader=true
FROM access_role r
WHERE tm.role_id=r.id AND r.code='lead';

ALTER TABLE team_member DROP COLUMN IF EXISTS role_id;

ALTER TABLE team DROP COLUMN IF EXISTS default_role_id;

-- Keep historical role rows for invite/audit references, but disable them as
-- assignable global roles. New deployments do not create this role.
UPDATE access_role SET team_only=true WHERE code='lead';

