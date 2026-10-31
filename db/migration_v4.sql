-- Feedback loop v4: receipts, ownership, idempotency and knowledge adoption.

ALTER TABLE audit_log
  ADD COLUMN IF NOT EXISTS feedback_verdict TEXT;
ALTER TABLE audit_log
  ADD COLUMN IF NOT EXISTS feedback_at TIMESTAMPTZ;
ALTER TABLE audit_log
  ADD COLUMN IF NOT EXISTS feedback_by TEXT;
ALTER TABLE audit_log
  ADD COLUMN IF NOT EXISTS feedback_by_account_id INT;

ALTER TABLE issue
  ADD COLUMN IF NOT EXISTS feedback_verdict TEXT;
ALTER TABLE issue
  ADD COLUMN IF NOT EXISTS suggested_answer TEXT;
ALTER TABLE issue
  ADD COLUMN IF NOT EXISTS created_by_account_id INT;
ALTER TABLE issue
  ADD COLUMN IF NOT EXISTS faq_version INT;
ALTER TABLE issue
  ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT now();

UPDATE audit_log
SET feedback_verdict=COALESCE(feedback_verdict,
                                user_feedback::jsonb->>'verdict')
WHERE user_feedback IS NOT NULL
  AND feedback_verdict IS NULL
  AND user_feedback LIKE '{%';

UPDATE issue
SET feedback_verdict=COALESCE(feedback_verdict,
                                feedback::jsonb->>'verdict')
WHERE feedback IS NOT NULL
  AND feedback_verdict IS NULL
  AND feedback LIKE '{%';

CREATE INDEX IF NOT EXISTS idx_audit_feedback_by
  ON audit_log (feedback_by_account_id, feedback_at DESC)
  WHERE user_feedback IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_issue_related_audit
  ON issue (related_audit, status);
