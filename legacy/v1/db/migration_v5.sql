-- Feedback v5: separate standard answers from reusable answer policies.

ALTER TABLE issue
  ADD COLUMN IF NOT EXISTS feedback_type TEXT
  NOT NULL DEFAULT 'improvement_suggestion';
ALTER TABLE issue
  ADD COLUMN IF NOT EXISTS answer_policy_id BIGINT;

UPDATE issue
SET feedback_type='standard_answer'
WHERE feedback_type='improvement_suggestion'
  AND NULLIF(trim(suggested_answer), '') IS NOT NULL;

CREATE TABLE IF NOT EXISTS answer_policy (
  id              BIGSERIAL PRIMARY KEY,
  name            TEXT NOT NULL,
  rule            TEXT NOT NULL,
  policy_kind     TEXT NOT NULL DEFAULT 'instruction'
                  CHECK (policy_kind IN
                         ('instruction','language_mirror','require_context')),
  applies_to      TEXT NOT NULL DEFAULT 'all',
  priority        INT NOT NULL DEFAULT 100,
  status          TEXT NOT NULL DEFAULT 'draft'
                  CHECK (status IN ('draft','active','deprecated','rejected')),
  source_issue_id BIGINT REFERENCES issue(id) ON DELETE SET NULL,
  created_by      TEXT,
  approved_by     TEXT,
  approved_at     TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE issue
  DROP CONSTRAINT IF EXISTS issue_answer_policy_fk;
ALTER TABLE issue
  ADD CONSTRAINT issue_answer_policy_fk
  FOREIGN KEY (answer_policy_id) REFERENCES answer_policy(id)
  ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_answer_policy_active
  ON answer_policy (status, priority, id);
CREATE INDEX IF NOT EXISTS idx_issue_feedback_type
  ON issue (feedback_type, status);

INSERT INTO answer_policy
  (name, rule, policy_kind, applies_to, priority, status, created_by,
   approved_by, approved_at)
SELECT
  '客户语言一致性保护',
  '客户使用中文时不得直接输出英文草稿；没有中文依据时应升级人工处理。',
  'language_mirror', 'all', 10, 'active', 'system', 'system', now()
WHERE NOT EXISTS (
  SELECT 1 FROM answer_policy
  WHERE policy_kind='language_mirror' AND status='active'
);
