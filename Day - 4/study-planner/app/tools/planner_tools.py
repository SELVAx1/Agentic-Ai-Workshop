"""The study planner's tools, split between two specialist agents.

    ScheduleTools  (read-only)  ── list_subjects, get_subject_slots, get_slot
    DeskTools      (read+write) ── get_student_schedule, check_can_book, book_slot,
                                   cancel_slot, notify_student

Tool descriptions are prompts: they say *when* to use, *when not to*, and *what changes*.
"""
from datetime import datetime, timezone

from app.idempotency import notification_dedupe_key
from app.planner_db import PlannerDb
from app.tools.dispatch import dispatch


# ──────────────────────────────────────────────────────────────────────────────
# Base class
# ──────────────────────────────────────────────────────────────────────────────

class Toolset:
    SIDE_EFFECTS: tuple[str, ...] = ()   # run through PlannerDb.once with an idempotency key
    DELEGATES: tuple[str, ...] = ()      # hand work to another agent
    TOOL_NAMES: tuple[str, ...] = ()

    def functions(self) -> dict:
        return {n: getattr(self, n) for n in self.TOOL_NAMES}

    def call(self, name: str, args: dict) -> dict:
        return dispatch(self.functions(), name, args)


# ──────────────────────────────────────────────────────────────────────────────
# Schedule agent tools (READ-ONLY)
# ──────────────────────────────────────────────────────────────────────────────

class ScheduleTools(Toolset):
    """Read-only. The schedule agent can look at subjects and slots, never change anything."""

    TOOL_NAMES = ("list_subjects", "get_subject_slots", "get_slot")

    def __init__(self, db: PlannerDb):
        self.db = db

    def list_subjects(self, text: str = "") -> dict:
        """Find subjects available in the study planner by name, code, or keyword.

        Use for "what subjects are there", "show me CS subjects", "subjects about databases".
        Returns at most ten matches. Read-only: changes nothing. To book a slot, that is the
        desk's job — first find the subject here, then get its slots.

        Args:
            text: Optional search words from the subject name, code, or description.
                  Pass an empty string to list all subjects.

        Returns:
            {"subjects": [{"subject_id", "code", "name", "credits", "instructor",
                           "total_seats", "description"}]}.
        """
        subjects = self.db.list_subjects(text)
        return {"subjects": [
            {"subject_id": s["id"], "code": s["code"], "name": s["name"],
             "credits": s["credits"], "instructor": s["instructor"],
             "total_seats": s["total_seats"], "description": s["description"]}
            for s in subjects
        ]}

    def get_subject_slots(self, subject_id: int) -> dict:
        """Get all upcoming study slots for a specific subject, including seat availability.

        Use when you have a subject_id from list_subjects and need to show available times.
        Read-only: changes nothing.

        Args:
            subject_id: Integer id returned by list_subjects.

        Returns:
            {"subject_id", "subject_name", "slots": [{"slot_id", "slot_date", "slot_time",
              "seats_total", "seats_available"}]}.
        """
        subj = self.db.get_subject(subject_id)
        if subj is None:
            return {"error": "unknown_subject",
                    "hint": "Use list_subjects to find the correct subject_id."}
        slots = self.db.get_slots_for_subject(subject_id)
        return {
            "subject_id": subject_id,
            "subject_name": subj["name"],
            "slots": [
                {"slot_id": sl["id"], "slot_date": sl["slot_date"],
                 "slot_time": sl["slot_time"], "seats_total": sl["seats_total"],
                 "seats_available": sl["seats_available"]}
                for sl in slots
            ]
        }

    def get_slot(self, slot_id: int) -> dict:
        """Get full details of one study slot: subject, date, time, and current seat count.

        Use when you already have a slot_id from get_subject_slots and want its current
        availability. Read-only: changes nothing.

        Args:
            slot_id: Integer id returned by get_subject_slots.

        Returns:
            {"slot_id", "subject_id", "subject_name", "slot_date", "slot_time",
             "seats_total", "seats_available"}.
        """
        sl = self.db.get_slot(slot_id)
        if sl is None:
            return {"error": "unknown_slot",
                    "hint": "Use get_subject_slots to find the correct slot_id."}
        subj = self.db.get_subject(sl["subject_id"])
        return {
            "slot_id": sl["id"], "subject_id": sl["subject_id"],
            "subject_name": subj["name"] if subj else "?",
            "slot_date": sl["slot_date"], "slot_time": sl["slot_time"],
            "seats_total": sl["seats_total"], "seats_available": sl["seats_available"],
        }


# ──────────────────────────────────────────────────────────────────────────────
# Desk agent tools (READ + WRITE, bound to one student)
# ──────────────────────────────────────────────────────────────────────────────

class DeskTools(Toolset):
    """The booking desk, bound to ONE student. The model cannot pick a different student id."""

    TOOL_NAMES = ("get_student_schedule", "check_can_book",
                  "book_slot", "cancel_slot", "notify_student")
    SIDE_EFFECTS = ("book_slot", "cancel_slot", "notify_student")

    def __init__(self, db: PlannerDb, student_id: str,
                 clock=lambda: datetime.now(timezone.utc)):
        self.db = db
        self.student_id = student_id
        self.clock = clock

    def _student(self) -> dict:
        s = self.db.get_student(self.student_id)
        if s is None:
            raise LookupError(f"student {self.student_id} not found")
        return s

    # ── reads ────────────────────────────────────────────────────────────────

    def get_student_schedule(self) -> dict:
        """Get the current student's profile and all their active slot bookings.

        Use for "what have I booked", "show my schedule", "what slots am I in".
        Read-only: changes nothing.

        Returns:
            {"student_id", "name", "dept", "max_daily_slots",
             "bookings": [{"booking_id", "slot_id", "subject_code", "subject_name",
                           "slot_date", "slot_time"}]}.
        """
        s = self._student()
        bookings = self.db.active_bookings(s["id"])
        return {
            "student_id": s["student_id"],
            "name": s["name"],
            "dept": s["dept"],
            "max_daily_slots": s["max_daily_slots"],
            "bookings": [dict(b) for b in bookings],
        }

    def check_can_book(self, slot_id: int) -> dict:
        """Check whether the current student is allowed to book a specific slot, using the policy.

        Use BEFORE book_slot, and whenever the student asks "can I book this slot?".
        The decision comes from the policy table and seat count — never decide it yourself.
        Read-only: changes nothing.

        Args:
            slot_id: The slot the student wants to book (from get_subject_slots).

        Returns:
            {"can_book": bool, "reasons": [str]}.
            Every string in reasons is a rule that currently blocks the booking.
        """
        s = self._student()
        reasons = []

        sl = self.db.get_slot(slot_id)
        if sl is None:
            return {"can_book": False, "reasons": ["slot not found"]}

        # Rule 1 (from policy table): daily limit
        daily_limit = self.db.policy("max_slots_per_day")
        booked_today = self.db.bookings_on_day(s["id"], sl["slot_date"])
        if booked_today >= daily_limit:
            reasons.append(
                f"already has {booked_today} booking(s) on {sl['slot_date']},"
                f" the daily limit from policy is {daily_limit}")

        # Rule 2: no seats left
        if sl["seats_available"] <= 0:
            reasons.append(f"slot {slot_id} has no seats available")

        return {"can_book": not reasons, "reasons": reasons}

    # ── writes ───────────────────────────────────────────────────────────────

    def book_slot(self, slot_id: int) -> dict:
        """Book the specified study slot for the current student. CHANGES DATA: takes a seat.

        Use only after check_can_book returned can_book=true and the student confirmed.
        Booking the same slot again is safe and returns the existing booking.

        Args:
            slot_id: Integer id from get_subject_slots.

        Returns:
            {"slot_id", "subject_name", "slot_date", "slot_time",
             "status": "booked" | "already_booked"}, or
            {"error": "not_allowed" | "unknown_slot" | "no_seats", ...}.
        """
        verdict = self.check_can_book(slot_id)
        if not verdict["can_book"]:
            return {"error": "not_allowed", "reasons": verdict["reasons"],
                    "hint": "Explain the reasons to the student. Do not retry."}

        sl = self.db.get_slot(slot_id)
        if sl is None:
            return {"error": "unknown_slot",
                    "hint": "Ask the schedule specialist for the correct slot_id."}

        s = self._student()
        status = self.db.book_slot(s["id"], slot_id)

        if status == "no_seats":
            return {"error": "no_seats",
                    "hint": "No seat is available in this slot. Tell the student; do not retry."}

        subj = self.db.get_subject(sl["subject_id"])
        return {
            "slot_id": slot_id,
            "subject_name": subj["name"] if subj else "?",
            "slot_date": sl["slot_date"],
            "slot_time": sl["slot_time"],
            "status": status,   # "booked" or "already_booked"
        }

    def cancel_slot(self, slot_id: int) -> dict:
        """Cancel the current student's booking for the specified slot. CHANGES DATA: frees a seat.

        Use only when the student explicitly asks to cancel. Cancelling a booking that was
        already cancelled is safe and returns already_cancelled.

        Args:
            slot_id: Integer id of the slot to cancel (from get_student_schedule).

        Returns:
            {"slot_id", "status": "cancelled" | "already_cancelled"}, or
            {"error": "not_found", ...}.
        """
        s = self._student()
        status = self.db.cancel_booking(s["id"], slot_id)
        if status == "not_found":
            return {"error": "not_found",
                    "hint": "The student has no booking for this slot. Check get_student_schedule."}
        return {"slot_id": slot_id, "status": status}

    def notify_student(self, message: str) -> dict:
        """Send the current student a short notification. CHANGES DATA: a message goes out.

        Use to confirm something that just happened — a booking, a cancellation.
        The same message on the same day is sent only once (deduplication).
        Never use this to answer a question; reply in chat instead.

        Args:
            message: 1 to 160 characters.

        Returns:
            {"notification_id", "status": "queued", "duplicate": bool}.
        """
        if not message.strip() or len(message) > 160:
            return {"error": "invalid_message",
                    "hint": "message must be between 1 and 160 characters."}
        key = notification_dedupe_key(self.student_id, message, self.clock().date())
        notification_id, created = self.db.record_notification(self.student_id, message, key)
        return {"notification_id": notification_id, "status": "queued", "duplicate": not created}
