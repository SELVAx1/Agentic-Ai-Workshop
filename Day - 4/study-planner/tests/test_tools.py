"""Tools, data-enforced business rules, and safe-to-repeat writes."""
import inspect

import pytest

from app.tools.planner_tools import DeskTools, ScheduleTools


# ── Every tool must have a proper description ─────────────────────────────────

@pytest.mark.parametrize("cls", [ScheduleTools, DeskTools])
def test_every_tool_is_described(cls, db):
    """Each tool docstring must be at least 120 chars (it IS the prompt)."""
    instance = cls(db) if cls is ScheduleTools else cls(db, "22CS045")
    for name in cls.TOOL_NAMES:
        doc = inspect.getdoc(getattr(cls, name)) or ""
        assert len(doc) >= 120, f"{cls.__name__}.{name} needs a longer description"


# ── Schedule tools (read-only) ────────────────────────────────────────────────

def test_list_subjects_returns_all_when_no_filter(db):
    result = ScheduleTools(db).list_subjects()
    assert len(result["subjects"]) == 5


def test_list_subjects_filters_by_keyword(db):
    result = ScheduleTools(db).list_subjects("Machine Learning")
    assert len(result["subjects"]) == 1
    assert result["subjects"][0]["code"] == "CS401"


def test_list_subjects_empty_query_still_returns_results(db):
    result = ScheduleTools(db).list_subjects("  ")
    assert len(result["subjects"]) > 0


def test_get_subject_slots_returns_slots_for_known_subject(db):
    result = ScheduleTools(db).get_subject_slots(1)   # Data Structures
    assert result["subject_name"] == "Data Structures"
    assert len(result["slots"]) >= 2


def test_get_subject_slots_unknown_id_returns_error(db):
    assert ScheduleTools(db).get_subject_slots(999)["error"] == "unknown_subject"


def test_get_slot_returns_seat_count(db):
    result = ScheduleTools(db).get_slot(1)   # slot 1: CS301 on 2026-09-22 09:00
    assert result["seats_available"] == 30
    assert result["slot_date"] == "2026-09-22"


def test_get_slot_unknown_id_returns_error(db):
    assert ScheduleTools(db).get_slot(999)["error"] == "unknown_slot"


# ── Desk tools – reads ────────────────────────────────────────────────────────

def test_get_student_schedule_shows_existing_booking(db):
    # Divya (22EC031) already has slot 3 from seed data
    result = DeskTools(db, "22EC031").get_student_schedule()
    assert result["name"] == "Divya Sekar"
    assert len(result["bookings"]) == 1
    assert result["bookings"][0]["slot_id"] == 3


def test_policy_comes_from_the_database(db):
    """Changing the policy row changes the check outcome — the rule is in data, not the prompt."""
    # Arjun has 0 bookings; slot 1 is on 2026-09-22
    desk = DeskTools(db, "22IT017")
    assert desk.check_can_book(1)["can_book"] is True

    # Drop the daily limit to 0 → should now be blocked
    db.conn.execute("UPDATE policy SET value = 0 WHERE name = 'max_slots_per_day'")
    db.conn.commit()
    result = desk.check_can_book(1)
    assert result["can_book"] is False
    assert any("policy" in r for r in result["reasons"])


# ── Desk tools – writes ───────────────────────────────────────────────────────

def test_book_slot_takes_a_seat(db):
    desk = DeskTools(db, "22CS045")
    result = desk.book_slot(1)
    assert result["status"] == "booked"
    assert db.get_slot(1)["seats_available"] == 29


def test_booking_same_slot_twice_is_not_an_error(db):
    desk = DeskTools(db, "22CS045")
    assert desk.book_slot(2)["status"] == "booked"
    assert desk.book_slot(2)["status"] == "already_booked"
    assert db.get_slot(2)["seats_available"] == 29   # seat taken only once


def test_book_slot_refuses_when_daily_limit_exceeded(db):
    """book_slot must enforce the policy table even if check_can_book is never called."""
    db.conn.execute("UPDATE policy SET value = 0 WHERE name = 'max_slots_per_day'")
    db.conn.commit()
    result = DeskTools(db, "22CS045").book_slot(1)
    assert result["error"] == "not_allowed"
    assert db.count("booking") == 1   # seed has 1; no new booking added


def test_cancel_slot_frees_a_seat(db):
    # First book it
    DeskTools(db, "22CS045").book_slot(1)
    seats_after_book = db.get_slot(1)["seats_available"]
    DeskTools(db, "22CS045").cancel_slot(1)
    assert db.get_slot(1)["seats_available"] == seats_after_book + 1


def test_cancel_already_cancelled_is_safe(db):
    DeskTools(db, "22CS045").book_slot(1)
    DeskTools(db, "22CS045").cancel_slot(1)
    result = DeskTools(db, "22CS045").cancel_slot(1)
    assert result["status"] == "already_cancelled"


def test_same_notification_same_day_sent_once(db):
    desk = DeskTools(db, "22CS045")
    first  = desk.notify_student("Your booking is confirmed.")
    second = desk.notify_student("Your booking is confirmed.")
    assert first["notification_id"] == second["notification_id"]
    assert second["duplicate"] is True
    assert db.count("notification") == 1


def test_desk_tools_have_no_student_id_parameter(db):
    """The model must not be able to act for a different student by passing student_id as an arg."""
    params = {
        name: list(inspect.signature(getattr(DeskTools, name)).parameters)
        for name in DeskTools.TOOL_NAMES
    }
    assert all("student_id" not in p for p in params.values())
