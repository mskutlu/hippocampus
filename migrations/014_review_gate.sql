-- Migration 014: review gate for auto-generated fragments + supersede links.
--
-- review_state  'candidate' fragments (session summaries, auto-remembered
--               rules) stay recallable but are never injected or auto-pinned
--               until a human approves or pins them.
-- superseded_by id of the fragment that replaces this one; superseded
--               fragments are no longer recalled or injected.
--
-- Backfill: auto-generated fragments become candidates unless the user pinned
-- them deliberately. A pin is deliberate only when a 'pin' feedback row exists
-- with no later 'auto-pin' row. Every other pinned auto-generated fragment is
-- un-pinned and an 'unpin' row records why.

ALTER TABLE fragments ADD COLUMN review_state TEXT NOT NULL DEFAULT 'approved'
    CHECK (review_state IN ('candidate', 'approved'));
ALTER TABLE fragments ADD COLUMN superseded_by TEXT;

CREATE INDEX IF NOT EXISTS idx_fragments_review_state ON fragments(review_state);

CREATE TEMP TABLE review_gate_auto_pinned AS
SELECT f.id
FROM fragments f
WHERE f.source_type IN ('session-summary', 'auto-remembered')
  AND f.pinned = 1
  AND NOT EXISTS (
      SELECT 1 FROM feedback_log p
      WHERE p.fragment_id = f.id
        AND p.kind = 'pin'
        AND NOT EXISTS (
            SELECT 1 FROM feedback_log a
            WHERE a.fragment_id = f.id AND a.kind = 'auto-pin' AND a.id > p.id
        )
  );

INSERT INTO feedback_log (fragment_id, kind, reason)
SELECT id, 'unpin', 'review-gate-migration' FROM review_gate_auto_pinned;

UPDATE fragments SET pinned = 0
WHERE id IN (SELECT id FROM review_gate_auto_pinned);

UPDATE fragments SET review_state = 'candidate'
WHERE source_type IN ('session-summary', 'auto-remembered') AND pinned = 0;

DROP TABLE review_gate_auto_pinned;

INSERT OR IGNORE INTO schema_migrations(version) VALUES (14);
