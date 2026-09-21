"""planner.db: students, subjects, slots, bookings.

Every SQL statement for the study planner domain lives here.

SQLite is used by default (tests, demo, offline use).
To connect to Supabase (PostgreSQL), set:

    PLANNER_DB_URL=postgresql://postgres:<password>@<host>:5432/postgres

When that variable is set, this module uses psycopg2 instead of SQLite.
"""

import json
import os
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path


SCHEMA_PATH = Path(__file__).resolve().parent.parent / "schema" / "planner.sql"


# ---------------------------------------------------------------------------
# Backend detection
# ---------------------------------------------------------------------------

_DB_URL = os.environ.get("PLANNER_DB_URL", "")


def _is_postgres() -> bool:
    return (
        _DB_URL.startswith("postgresql://")
        or _DB_URL.startswith("postgres://")
    )


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

def _sqlite_connect(path: str):
    import sqlite3

    conn = sqlite3.connect(
        path,
        check_same_thread=False,
    )

    conn.row_factory = sqlite3.Row

    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    return conn


@contextmanager
def _sqlite_transaction(conn):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# PostgreSQL / Supabase helpers
# ---------------------------------------------------------------------------

def _pg_connect():
    """Return a psycopg2 connection using PLANNER_DB_URL."""

    try:
        import psycopg2
        import psycopg2.extras
    except ImportError as exc:
        raise ImportError(
            "psycopg2 is required for PostgreSQL/Supabase. "
            "Run: pip install psycopg2-binary"
        ) from exc

    conn = psycopg2.connect(
        _DB_URL,
        cursor_factory=psycopg2.extras.RealDictCursor,
    )

    conn.autocommit = False

    return conn


@contextmanager
def _pg_transaction(conn):
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


# ---------------------------------------------------------------------------
# Row compatibility
# ---------------------------------------------------------------------------

class _DictRow(dict):
    """dict that also supports attribute access for compatibility."""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(item)


def _row(r) -> _DictRow | None:
    return _DictRow(r) if r is not None else None


def _rows(rs) -> list[_DictRow]:
    return [_DictRow(r) for r in rs]


# ---------------------------------------------------------------------------
# PlannerDb
# ---------------------------------------------------------------------------

class PlannerDb:
    """All domain queries for the study planner.

    Transparently switches between SQLite and PostgreSQL (Supabase)
    depending on whether PLANNER_DB_URL is set.
    """

    def __init__(
        self,
        path: str = ":memory:",
        clock: Callable[[], float] = time.time,
    ):
        self._use_pg = _is_postgres()

        if self._use_pg:
            self.conn = _pg_connect()
        else:
            self.conn = _sqlite_connect(path)

        self.clock = clock

    # -----------------------------------------------------------------------
    # Transactions
    # -----------------------------------------------------------------------

    def transaction(self):
        if self._use_pg:
            return _pg_transaction(self.conn)

        return _sqlite_transaction(self.conn)

    # -----------------------------------------------------------------------
    # Generic SQL helpers
    # -----------------------------------------------------------------------

    def _exec(self, sql: str, params=()):
        """Execute SQL for either SQLite or PostgreSQL."""

        if self._use_pg:
            # Application SQL uses SQLite-style ? placeholders.
            # psycopg2 requires %s.
            sql = sql.replace("?", "%s")

            cur = self.conn.cursor()
            cur.execute(sql, params)

            return cur

        return self.conn.execute(sql, params)

    def _fetchone(self, sql: str, params=()):
        cur = self._exec(sql, params)

        r = cur.fetchone()

        return _row(r)

    def _fetchall(self, sql: str, params=()):
        cur = self._exec(sql, params)

        return _rows(cur.fetchall())

    # -----------------------------------------------------------------------
    # Migration
    # -----------------------------------------------------------------------

    def migrate(self) -> None:
        """Create tables and seed data if the database is empty."""

        schema = SCHEMA_PATH.read_text(encoding="utf-8")

        if self._use_pg:
            self._migrate_postgres(schema)
        else:
            self.conn.executescript(schema)

        # ---------------------------------------------------------------
        # Seed only when student table is empty
        # ---------------------------------------------------------------

        existing = self._fetchone(
            "SELECT count(*) as n FROM student"
        )

        if existing["n"]:
            return

        with self.transaction():
            # -----------------------------------------------------------
            # Three students
            # -----------------------------------------------------------

            students = [
                (1, "22CS045", "Priya Raman", "CSE", 3),
                (2, "22IT017", "Arjun Kumar", "IT", 3),
                (3, "22EC031", "Divya Sekar", "ECE", 2),
            ]

            for student in students:
                self._exec(
                    """
                    INSERT INTO student
                        (id, student_id, name, dept, max_daily_slots)
                    VALUES
                        (?, ?, ?, ?, ?)
                    """,
                    student,
                )

            # -----------------------------------------------------------
            # Five subjects
            # -----------------------------------------------------------

            subjects = [
                (
                    1,
                    "CS301",
                    "Data Structures",
                    3,
                    "Dr. Meera Nair",
                    30,
                    "Core CS subject covering arrays, trees, graphs",
                ),
                (
                    2,
                    "CS401",
                    "Machine Learning",
                    4,
                    "Dr. Ravi Shankar",
                    25,
                    "Introduction to ML algorithms and applications",
                ),
                (
                    3,
                    "IT201",
                    "Database Management",
                    3,
                    "Dr. Latha Suresh",
                    28,
                    "SQL, normalization, transactions",
                ),
                (
                    4,
                    "EC301",
                    "Digital Signal Processing",
                    3,
                    "Dr. Anand Rao",
                    20,
                    "Signals, filters, Fourier transforms",
                ),
                (
                    5,
                    "CS501",
                    "Operating Systems",
                    3,
                    "Dr. Priya Das",
                    30,
                    "Process management, memory, file systems",
                ),
            ]

            for subject in subjects:
                self._exec(
                    """
                    INSERT INTO subject
                        (
                            id,
                            code,
                            name,
                            credits,
                            instructor,
                            total_seats,
                            description
                        )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    subject,
                )

            # -----------------------------------------------------------
            # Policy
            # -----------------------------------------------------------

            self._exec(
                """
                INSERT INTO policy (name, value)
                VALUES (?, ?)
                """,
                ("max_slots_per_day", 3),
            )

            # -----------------------------------------------------------
            # Study slots
            # -----------------------------------------------------------

            slots = [
                (1, 1, "2026-09-22", "09:00", 30, 30),
                (2, 1, "2026-09-22", "14:00", 30, 30),
                (3, 1, "2026-09-23", "09:00", 30, 28),
                (4, 2, "2026-09-22", "10:00", 25, 25),
                (5, 2, "2026-09-23", "10:00", 25, 23),
                (6, 3, "2026-09-22", "11:00", 28, 28),
                (7, 3, "2026-09-23", "11:00", 28, 27),
                (8, 4, "2026-09-22", "13:00", 20, 20),
                (9, 4, "2026-09-23", "13:00", 20, 19),
                (10, 5, "2026-09-22", "15:00", 30, 30),
                (11, 5, "2026-09-23", "15:00", 30, 29),
            ]

            for slot in slots:
                self._exec(
                    """
                    INSERT INTO study_slot
                        (
                            id,
                            subject_id,
                            slot_date,
                            slot_time,
                            seats_total,
                            seats_available
                        )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    slot,
                )

            # -----------------------------------------------------------
            # Divya already booked slot 3
            # -----------------------------------------------------------

            self._exec(
                """
                INSERT INTO booking
                    (student_id, slot_id, booked_at, status)
                VALUES (?, ?, ?, ?)
                """,
                (
                    3,
                    3,
                    self.clock(),
                    "active",
                ),
            )

            # One seat was consumed by Divya.
            self._exec(
                """
                UPDATE study_slot
                SET seats_available = seats_available - 1
                WHERE id = 3
                """
            )

    # -----------------------------------------------------------------------
    # PostgreSQL-specific migration
    # -----------------------------------------------------------------------

    def _migrate_postgres(self, schema: str) -> None:
        """Create the SQLite-compatible schema in PostgreSQL.

        PostgreSQL does not treat:

            INTEGER PRIMARY KEY

        as an auto-incrementing column.

        SQLite does.

        Therefore PostgreSQL tables need identity columns so inserts that
        omit the ID can automatically receive one.
        """

        import re

        # ---------------------------------------------------------------
        # Convert SQLite's INTEGER PRIMARY KEY behavior to PostgreSQL
        # identity behavior.
        #
        # BY DEFAULT is intentional:
        #
        #   INSERT INTO student (id, ...) VALUES (1, ...)
        #
        # must still work for the seed data.
        # ---------------------------------------------------------------

        pg_schema = re.sub(
            r"\bINTEGER\s+PRIMARY\s+KEY\b",
            "INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
            schema,
            flags=re.IGNORECASE,
        )

        # ---------------------------------------------------------------
        # Split schema into statements.
        # ---------------------------------------------------------------

        statements = [
            statement.strip()
            for statement in re.split(
                r";\s*(?:\n|$)",
                pg_schema,
            )
            if statement.strip()
        ]

        cur = self.conn.cursor()

        try:
            for statement in statements:
                try:
                    cur.execute(statement)

                except Exception as exc:
                    # If the table already exists, CREATE TABLE IF NOT
                    # EXISTS should normally prevent an error.
                    #
                    # We don't silently ignore other PostgreSQL errors.
                    self.conn.rollback()
                    raise RuntimeError(
                        "PostgreSQL migration failed.\n\n"
                        f"SQL:\n{statement}\n\n"
                        f"Error:\n{exc}"
                    ) from exc

            self.conn.commit()

        finally:
            cur.close()

        # ---------------------------------------------------------------
        # IMPORTANT:
        #
        # You already ran the old migration once.
        #
        # Therefore Supabase may already contain:
        #
        #     id INTEGER PRIMARY KEY
        #
        # instead of:
        #
        #     id INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY
        #
        # ALTER the existing tables when necessary.
        # ---------------------------------------------------------------

        identity_tables = [
            "student",
            "subject",
            "study_slot",
            "booking",
            "notification",
        ]

        for table in identity_tables:
            self._ensure_identity_column(table)

        self.conn.commit()

    def _ensure_identity_column(self, table: str) -> None:
        """Ensure an existing PostgreSQL ID column generates IDs."""

        # Table names come only from the fixed internal list above.
        allowed = {
            "student",
            "subject",
            "study_slot",
            "booking",
            "notification",
        }

        if table not in allowed:
            raise ValueError(f"Invalid table name: {table}")

        # Check whether the column already has an identity definition.
        row = self._fetchone(
            """
            SELECT is_identity
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = ?
              AND column_name = 'id'
            """,
            (table,),
        )

        if row is None:
            return

        if row["is_identity"] == "YES":
            return

        # Existing table was created using the old SQLite-style schema.
        #
        # Add PostgreSQL identity behavior.
        try:
            self._exec(
                f"""
                ALTER TABLE {table}
                ALTER COLUMN id
                ADD GENERATED BY DEFAULT AS IDENTITY
                """
            )

            self.conn.commit()

        except Exception as exc:
            self.conn.rollback()

            # It is possible that another migration mechanism already
            # created a sequence/default. Check before failing.
            default_row = self._fetchone(
                """
                SELECT column_default
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = ?
                  AND column_name = 'id'
                """,
                (table,),
            )

            if (
                default_row
                and default_row["column_default"]
                and "nextval" in default_row["column_default"]
            ):
                return

            raise RuntimeError(
                f"Could not configure auto-generated ID for "
                f"table '{table}'. Error: {exc}"
            ) from exc

    # -----------------------------------------------------------------------
    # Reads
    # -----------------------------------------------------------------------

    def get_student(self, student_id: str) -> _DictRow | None:
        return self._fetchone(
            """
            SELECT *
            FROM student
            WHERE student_id = ?
            """,
            (student_id,),
        )

    def policy(self, name: str) -> int:
        row = self._fetchone(
            """
            SELECT value
            FROM policy
            WHERE name = ?
            """,
            (name,),
        )

        return row["value"]

    def list_subjects(
        self,
        text: str = "",
        limit: int = 10,
    ) -> list[_DictRow]:

        if text.strip():
            like = f"%{text.strip()}%"

            return self._fetchall(
                """
                SELECT *
                FROM subject
                WHERE name LIKE ?
                   OR code LIKE ?
                   OR description LIKE ?
                ORDER BY code
                LIMIT ?
                """,
                (
                    like,
                    like,
                    like,
                    limit,
                ),
            )

        return self._fetchall(
            """
            SELECT *
            FROM subject
            ORDER BY code
            LIMIT ?
            """,
            (limit,),
        )

    def get_subject(self, subject_id: int) -> _DictRow | None:
        return self._fetchone(
            """
            SELECT *
            FROM subject
            WHERE id = ?
            """,
            (subject_id,),
        )

    def get_slots_for_subject(
        self,
        subject_id: int,
    ) -> list[_DictRow]:

        return self._fetchall(
            """
            SELECT *
            FROM study_slot
            WHERE subject_id = ?
            ORDER BY slot_date, slot_time
            """,
            (subject_id,),
        )

    def get_slot(self, slot_id: int) -> _DictRow | None:
        return self._fetchone(
            """
            SELECT *
            FROM study_slot
            WHERE id = ?
            """,
            (slot_id,),
        )

    def active_bookings(
        self,
        student_db_id: int,
    ) -> list[_DictRow]:

        return self._fetchall(
            """
            SELECT
                b.id AS booking_id,
                b.slot_id,
                ss.slot_date,
                ss.slot_time,
                s.code AS subject_code,
                s.name AS subject_name
            FROM booking b
            JOIN study_slot ss
                ON ss.id = b.slot_id
            JOIN subject s
                ON s.id = ss.subject_id
            WHERE b.student_id = ?
              AND b.status = 'active'
            ORDER BY ss.slot_date, ss.slot_time
            """,
            (student_db_id,),
        )

    def bookings_on_day(
        self,
        student_db_id: int,
        date: str,
    ) -> int:

        row = self._fetchone(
            """
            SELECT count(*) AS n
            FROM booking b
            JOIN study_slot ss
                ON ss.id = b.slot_id
            WHERE b.student_id = ?
              AND ss.slot_date = ?
              AND b.status = 'active'
            """,
            (
                student_db_id,
                date,
            ),
        )

        return row["n"]

    def count(self, table: str) -> int:
        assert table.isidentifier()

        row = self._fetchone(
            f"""
            SELECT count(*) AS n
            FROM {table}
            """
        )

        return row["n"]

    # -----------------------------------------------------------------------
    # Safe writes
    # -----------------------------------------------------------------------

    def book_slot(
        self,
        student_db_id: int,
        slot_id: int,
    ) -> str:

        """Returns 'booked', 'already_booked', or 'no_seats'.

        Safe to repeat (idempotent).
        """

        with self.transaction():

            existing = self._fetchone(
                """
                SELECT status
                FROM booking
                WHERE student_id = ?
                  AND slot_id = ?
                """,
                (
                    student_db_id,
                    slot_id,
                ),
            )

            if existing and existing["status"] == "active":
                return "already_booked"

            slot = self._fetchone(
                """
                SELECT seats_available, version
                FROM study_slot
                WHERE id = ?
                """,
                (slot_id,),
            )

            if slot is None:
                return "not_found"

            took = self._exec(
                """
                UPDATE study_slot
                SET
                    seats_available = seats_available - 1,
                    version = version + 1
                WHERE id = ?
                  AND seats_available > 0
                  AND version = ?
                """,
                (
                    slot_id,
                    slot["version"],
                ),
            ).rowcount

            if not took:
                return "no_seats"

            if existing and existing["status"] == "cancelled":

                # Re-activate a previously cancelled booking.
                self._exec(
                    """
                    UPDATE booking
                    SET
                        status = 'active',
                        booked_at = ?
                    WHERE student_id = ?
                      AND slot_id = ?
                    """,
                    (
                        self.clock(),
                        student_db_id,
                        slot_id,
                    ),
                )

            else:

                # PostgreSQL automatically generates booking.id.
                self._exec(
                    """
                    INSERT INTO booking
                        (
                            student_id,
                            slot_id,
                            booked_at,
                            status
                        )
                    VALUES
                        (?, ?, ?, 'active')
                    """,
                    (
                        student_db_id,
                        slot_id,
                        self.clock(),
                    ),
                )

            return "booked"

    def cancel_booking(
        self,
        student_db_id: int,
        slot_id: int,
    ) -> str:

        """Returns 'cancelled', 'not_found', or 'already_cancelled'.

        Safe to repeat.
        """

        with self.transaction():

            row = self._fetchone(
                """
                SELECT status
                FROM booking
                WHERE student_id = ?
                  AND slot_id = ?
                """,
                (
                    student_db_id,
                    slot_id,
                ),
            )

            if row is None:
                return "not_found"

            if row["status"] == "cancelled":
                return "already_cancelled"

            self._exec(
                """
                UPDATE booking
                SET status = 'cancelled'
                WHERE student_id = ?
                  AND slot_id = ?
                """,
                (
                    student_db_id,
                    slot_id,
                ),
            )

            self._exec(
                """
                UPDATE study_slot
                SET seats_available = seats_available + 1
                WHERE id = ?
                """,
                (slot_id,),
            )

            return "cancelled"

    # -----------------------------------------------------------------------
    # Notifications
    # -----------------------------------------------------------------------

    def record_notification(
        self,
        student_id: str,
        message: str,
        dedupe_key: str,
    ) -> tuple[int, bool]:

        """Insert a notification.

        Silently skips duplicates.

        Returns:
            (id, created)
        """

        try:

            if self._use_pg:

                # PostgreSQL does not provide SQLite's cursor.lastrowid.
                #
                # RETURNING id gives us the generated ID directly.
                cur = self._exec(
                    """
                    INSERT INTO notification
                        (
                            student_id,
                            message,
                            dedupe_key,
                            created_at
                        )
                    VALUES (?, ?, ?, ?)
                    RETURNING id
                    """,
                    (
                        student_id,
                        message,
                        dedupe_key,
                        self.clock(),
                    ),
                )

                row = cur.fetchone()

                self.conn.commit()

                return row["id"], True

            else:

                cur = self._exec(
                    """
                    INSERT INTO notification
                        (
                            student_id,
                            message,
                            dedupe_key,
                            created_at
                        )
                    VALUES (?, ?, ?, ?)
                    """,
                    (
                        student_id,
                        message,
                        dedupe_key,
                        self.clock(),
                    ),
                )

                self.conn.commit()

                return cur.lastrowid, True

        except Exception:

            self.conn.rollback()

            row = self._fetchone(
                """
                SELECT id
                FROM notification
                WHERE dedupe_key = ?
                """,
                (dedupe_key,),
            )

            if row is None:
                raise

            return row["id"], False

    # -----------------------------------------------------------------------
    # Idempotency
    # -----------------------------------------------------------------------

    def once(
        self,
        key: str,
        tool_name: str,
        effect: Callable[[], dict],
    ) -> tuple[dict, bool]:

        """Run a side effect at most once per idempotency key.

        The key and result commit together.
        """

        with self.transaction():

            row = self._fetchone(
                """
                SELECT result
                FROM idempotency
                WHERE key = ?
                """,
                (key,),
            )

            if row is not None:
                return json.loads(row["result"]), False

            result = effect()

            self._exec(
                """
                INSERT INTO idempotency
                    (
                        key,
                        tool_name,
                        result,
                        created_at
                    )
                VALUES (?, ?, ?, ?)
                """,
                (
                    key,
                    tool_name,
                    json.dumps(result, default=str),
                    self.clock(),
                ),
            )

            return result, True