"""App-level configuration. Reads from environment variables with sensible defaults."""
import os

from app.memory import RunStore
from app.planner_db import PlannerDb

# SQLite file paths (ignored when PLANNER_DB_URL points to Supabase/Postgres)
AGENT_DB    = os.environ.get("AGENT_DB",    "agent.db")
PLANNER_DB  = os.environ.get("PLANNER_DB",  "planner.db")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.6-flash")


def open_stores() -> tuple[RunStore, PlannerDb]:
    """Open and migrate both databases, return (store, db) ready to use."""
    store = RunStore(AGENT_DB)
    db    = PlannerDb(PLANNER_DB)
    store.migrate()
    db.migrate()
    return store, db


def make_providers(mock: bool, slow: float = 0.0) -> dict:
    """Return one provider per agent (supervisor, schedule, desk).

    mock=True  → scripted RoutedMock, no API key needed.
    mock=False → real Gemini; all three agents share one client.
    """
    if mock:
        from app.providers import demo_providers
        return demo_providers(slow)

    from app.providers import GeminiProvider
    gemini = GeminiProvider(GEMINI_MODEL)
    return {"supervisor": gemini, "schedule": gemini, "desk": gemini}
