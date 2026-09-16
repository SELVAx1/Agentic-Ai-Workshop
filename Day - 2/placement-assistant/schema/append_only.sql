-- Messages are append-only.
-- SQLite itself must reject UPDATE and DELETE operations.

CREATE TRIGGER IF NOT EXISTS message_no_update
BEFORE UPDATE ON message
BEGIN
    SELECT RAISE(ABORT, 'message rows are append-only');
END;

CREATE TRIGGER IF NOT EXISTS message_no_delete
BEFORE DELETE ON message
BEGIN
    SELECT RAISE(ABORT, 'message rows are append-only');
END;