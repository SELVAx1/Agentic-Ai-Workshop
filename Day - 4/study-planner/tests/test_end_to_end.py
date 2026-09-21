"""Queue → worker → supervisor → specialists → reply. Includes the crash-and-replay test."""
import pytest

from app.providers import demo_providers
from app.worker import Worker
from tests.conftest import SimulatedCrash

# The two demo questions
Q_BOOK   = ("22CS045",
             "Are there any Machine Learning slots available? "
             "Book the 10:00 slot on 2026-09-22 for me and send me a confirmation.")
Q_BLOCKED = ("22IT017",
              "I already have 3 bookings today. "
              "Can I book another Data Structures slot on 2026-09-22?")


def _ask(store, student_id, text):
    thread = store.create_thread(student_id)
    return thread, store.enqueue(thread, text, "mock")


# ── Happy-path ────────────────────────────────────────────────────────────────

def test_booking_question_goes_all_the_way_through(store, db):
    thread, run_id = _ask(store, *Q_BOOK)
    assert Worker(store, db, demo_providers(), worker_id="w").run_until_idle() == [(run_id, "succeeded")]
    run = store.get_run(run_id)
    tool_names = [s["tool_name"] for s in run["steps"] if s["kind"] == "tool"]
    assert "ask_schedule" in tool_names
    assert "ask_desk"     in tool_names
    reply = store.load_history(thread)[-1]["text"]
    assert "booked" in reply.lower() or "confirmed" in reply.lower() or "mock" in reply.lower()
    assert db.count("booking") == 2        # seed 1 + Priya's new booking
    assert db.count("notification") == 1


def test_policy_refusal_still_succeeds_as_a_run(store, db):
    thread, run_id = _ask(store, *Q_BLOCKED)
    Worker(store, db, demo_providers(), worker_id="w").run_until_idle()
    assert store.get_run(run_id)["status"] == "succeeded"
    # No new booking or notification should appear
    assert db.count("booking") == 1
    assert db.count("notification") == 0


# ── Crash and replay ──────────────────────────────────────────────────────────

def test_crash_after_booking_does_not_duplicate(store, db, clock):
    """The key test: worker-A dies right after book_slot commits but before the idempotency
    record is written. Worker-B picks up the same run and must replay without booking again."""
    _, run_id = _ask(store, *Q_BOOK)
    real_once = db.once

    def once_then_die(key, tool_name, effect):
        result = real_once(key, tool_name, effect)
        if tool_name == "book_slot":
            raise SimulatedCrash()
        return result

    db.once = once_then_die
    with pytest.raises(SimulatedCrash):
        Worker(store, db, demo_providers(), worker_id="A", lease_seconds=30).run_once()

    db.once = real_once
    assert db.count("booking") == 2        # seat taken
    assert store.get_run(run_id)["status"] == "running"

    # Let the lease expire so worker-B can claim
    clock.advance(31)
    results = Worker(store, db, demo_providers(), worker_id="B", lease_seconds=30).run_until_idle()
    assert results == [(run_id, "succeeded")]

    # Still exactly one new booking and one notification — no duplicates
    assert db.count("booking") == 2
    assert db.count("notification") == 1
    assert store.get_run(run_id)["attempts"] == 2


# ── Queue mechanics ───────────────────────────────────────────────────────────

def test_asking_twice_still_one_booking(store, db):
    """Two identical requests from the same student → same side effects, not doubled."""
    for _ in range(2):
        _ask(store, *Q_BOOK)
    Worker(store, db, demo_providers(), worker_id="w").run_until_idle()
    assert db.count("booking") == 2        # seed 1 + one real booking
    assert db.count("notification") == 1


def test_expired_lease_requeues_run(store, db, clock):
    """A run whose lease expires while running is put back in the queue."""
    _, run_id = _ask(store, *Q_BOOK)
    claimed = store.claim_next("worker-X", lease_seconds=10)
    assert claimed is not None
    clock.advance(11)
    reaped = store.reap_expired()
    assert run_id in reaped
    assert store.get_run(run_id)["status"] == "queued"
