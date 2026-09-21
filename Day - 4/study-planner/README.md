# Student Study Planner Assistant

An end-to-end agentic service that lets students find and book study slots for their subjects.
Built as a weekend project following the Campus Library Assistant pattern (Day 4).

---

## Domain

A student books a **study slot** (a subject + date + time block with a seat limit).

| The thing that can clash | How it is enforced |
|---|---|
| One student can't double-book the same slot | `UNIQUE (student_id, slot_id)` in `booking` |
| No more than N bookings per student per day | `max_slots_per_day` in the `policy` table |
| Seats can't go below zero | `seats_available >= 0` CHECK + optimistic locking |

---

## Architecture

```
student
  │
  ▼
supervisor agent          (SupervisorTools — only two delegation tools)
  │             │
  ▼             ▼
ask_schedule  ask_desk
  │             │
schedule      desk
specialist    specialist
(read-only)   (read + write)
  │             │
  ▼             ▼
list_subjects        get_student_schedule
get_subject_slots    check_can_book
get_slot             book_slot  ◀── side effect, idempotency key
                     cancel_slot ◀── side effect, idempotency key
                     notify_student ◀── side effect, deduped by day
```

Two SQLite databases (or Supabase — see below):
- **agent.db** — conversations, runs, the job queue, tool-call records
- **planner.db** — students, subjects, slots, bookings, policy, notifications, idempotency keys

---

## Quick start (no API key needed)

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Run the scripted demo — two questions, zero API calls
python -m scripts.demo

# 4. Run the crash-and-replay demo (prints PASS)
python -m scripts.demo --crash

# 5. Run all tests
pytest
```

---

## Scripts

| Command | What it does |
|---|---|
| `python -m scripts.demo` | Two scripted questions, no API key |
| `python -m scripts.demo --crash` | Crash demo: kills worker after booking, resumes, prints PASS |
| `python -m scripts.demo --real` | Same two questions on real Gemini (needs `GEMINI_API_KEY`) |
| `python -m scripts.worker` | Long-running worker (run in a separate terminal) |
| `python -m scripts.ask "question" --student 22CS045` | Queue a question (worker must be running) |

---

## Connecting to Supabase (MySQL-compatible PostgreSQL)

Supabase exposes a PostgreSQL endpoint. This project switches from SQLite to Supabase
automatically when `PLANNER_DB_URL` is set in the environment.

### Step-by-step

**1. Create a Supabase project**
- Go to [https://supabase.com](https://supabase.com) and sign in.
- Click **New project**, choose a name and a strong database password.
- Wait for the project to be ready (~1 minute).

**2. Get the connection string**
- In your project dashboard, go to **Settings → Database**.
- Under **Connection string**, choose **URI** and copy the value.
  It looks like:
  ```
  postgresql://postgres:<your-password>@db.<project-ref>.supabase.co:5432/postgres
  ```

**3. Install the PostgreSQL driver**
```bash
pip install psycopg2-binary==2.9.9
```
Or uncomment the line in `requirements.txt` and re-run `pip install -r requirements.txt`.

**4. Set the environment variable**

Windows PowerShell:
```powershell
$env:PLANNER_DB_URL = "postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres"
```

macOS / Linux:
```bash
export PLANNER_DB_URL="postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres"
```

**5. Run the migration (creates tables + seed data)**
```bash
python -c "from app.planner_db import PlannerDb; PlannerDb().migrate()"
```

**6. Run the demo against Supabase**
```bash
python -m scripts.demo
```

> **Note:** The agent's memory (agent.db) still uses SQLite by default.
> Set `AGENT_DB` to a local path or leave it as-is — it's only the planner's
> domain data (slots, bookings) that lives in Supabase.

### What the connection string looks like for MySQL (via a MySQL-to-Postgres proxy)

If you are using PlanetScale, Neon, or another MySQL-compatible service exposed over
a PostgreSQL wire protocol, the URL format is the same.  
For a native MySQL server, replace `PLANNER_DB_URL` with a MySQL DSN and install
`mysql-connector-python` or `pymysql`, then update `_pg_connect()` in `planner_db.py`
to use that driver instead of psycopg2.

---

## Seed data

| Students | Subjects | Slots |
|---|---|---|
| 22CS045 Priya Raman (CSE) | CS301 Data Structures | 3 slots across 2 days |
| 22IT017 Arjun Kumar (IT) | CS401 Machine Learning | 2 slots |
| 22EC031 Divya Sekar (ECE, 1 booking already) | IT201 Database Management | 2 slots |
| | EC301 Digital Signal Processing | 2 slots |
| | CS501 Operating Systems | 2 slots |

Policy: `max_slots_per_day = 3`

---

## Running on real Gemini

1. Get a key at [https://aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)
2. Set it: `$env:GEMINI_API_KEY = "your-key"`
3. Run: `python -m scripts.demo --real`

---

## Design choices

- **Business rules in data, not prompts.** The `policy` table controls the daily booking limit.
  `book_slot` enforces the rule even if the model skips `check_can_book`.
- **Idempotency at the tool layer.** Every side-effecting tool call is wrapped in `db.once(key, ...)`.
  The key and the result commit together, so a replayed run never double-books.
- **Least-privilege specialists.** The schedule agent has zero write tools.
  The desk agent is bound to one student at construction time — it can't act for anyone else.
- **Optimistic locking on seats.** `UPDATE study_slot ... WHERE version = ?` prevents two workers
  from both taking the last seat in a race.
- **Notification deduplication.** `notify_student` uses a content+date hash as a unique key,
  so the same message on the same day is stored and sent only once.

---

## What was left out

- A web UI / REST API (out of scope for the weekend project)
- Email/SMS delivery for notifications (the `notification` table is the outbox)
- Waitlist when a slot is full
