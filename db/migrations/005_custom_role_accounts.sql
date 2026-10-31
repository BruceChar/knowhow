-- Role identity is enforced by account.role_id. The legacy role code column
-- must accept custom role codes as well.
ALTER TABLE account DROP CONSTRAINT IF EXISTS account_role_check;

