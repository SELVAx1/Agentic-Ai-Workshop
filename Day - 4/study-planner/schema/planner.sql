-- planner.db: the study planner's domain data. Agent memory lives in agent.db.
--
-- Domain: a student books a study slot for a subject on a given date and time.
-- The thing that can clash: a student cannot book the same slot (subject + date + time) twice,
-- and the policy table caps how many slots a student can book per day.

CREATE TABLE IF NOT EXISTS student (
    id          INTEGER PRIMARY KEY,
    student_id  TEXT NOT NULL UNIQUE,   -- e.g. "22CS045"
    name        TEXT NOT NULL,
    dept        TEXT NOT NULL,
    max_daily_slots  INTEGER NOT NULL DEFAULT 3   -- overrideable per student
);

CREATE TABLE IF NOT EXISTS subject (
    id           INTEGER PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,  -- e.g. "CS301"
    name         TEXT NOT NULL,
    credits      INTEGER NOT NULL,
    instructor   TEXT NOT NULL,
    total_seats  INTEGER NOT NULL,      -- seats available per slot
    description  TEXT NOT NULL DEFAULT ''
);

-- Business rules live in data, not prompts.
-- max_slots_per_day: maximum bookings any student can make on a single calendar day.
CREATE TABLE IF NOT EXISTS policy (
    name   TEXT PRIMARY KEY,
    value  INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS study_slot (
    id          INTEGER PRIMARY KEY,
    subject_id  INTEGER NOT NULL REFERENCES subject (id),
    slot_date   TEXT NOT NULL,          -- ISO date string, e.g. "2026-09-22"
    slot_time   TEXT NOT NULL,          -- e.g. "09:00", "14:00"
    seats_total     INTEGER NOT NULL,
    seats_available INTEGER NOT NULL CHECK (seats_available >= 0),
    version         INTEGER NOT NULL DEFAULT 0,
    UNIQUE (subject_id, slot_date, slot_time)
);

CREATE TABLE IF NOT EXISTS booking (
    id          INTEGER PRIMARY KEY,
    student_id  INTEGER NOT NULL REFERENCES student (id),
    slot_id     INTEGER NOT NULL REFERENCES study_slot (id),
    booked_at   REAL NOT NULL,
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'cancelled')),
    UNIQUE (student_id, slot_id)
);

CREATE TABLE IF NOT EXISTS notification (
    id          INTEGER PRIMARY KEY,
    student_id  TEXT NOT NULL,
    message     TEXT NOT NULL,
    dedupe_key  TEXT NOT NULL UNIQUE,
    created_at  REAL NOT NULL
);

-- Idempotency keys live next to the side effects they guard.
CREATE TABLE IF NOT EXISTS idempotency (
    key         TEXT PRIMARY KEY,
    tool_name   TEXT NOT NULL,
    result      TEXT NOT NULL,
    created_at  REAL NOT NULL
);
