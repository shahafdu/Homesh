-- Per-user interface preferences: palette, appearance, view mode.
--
-- Stored server-side rather than in browser storage on purpose. The same account
-- drives a phone, a TV app and a desktop browser (ARCHITECTURE.md §5.8), and a
-- preference that only exists on one device is a preference you have to set three
-- times.

ALTER TABLE users
    ADD COLUMN IF NOT EXISTS prefs JSONB NOT NULL DEFAULT '{}'::jsonb;
