import operator
from datetime import datetime, timezone

from app.domain import AlreadyApplied, Rule, Student
from app.data import InMemoryPlacementRepo
from app.tools.dispatch import dispatch

OPS = {
    ">=": operator.ge,
    "<=": operator.le,
    "==": operator.eq,
    "in": lambda actual, allowed: actual in allowed.split(","),
}


def _passes(rule: Rule, student_value) -> bool:
    return OPS[rule.op](student_value, rule.typed_value())


def _unknown_student(roll_no: str) -> dict:
    return {
        "error": "unknown_student",
        "hint": f"No student with roll number {roll_no!r}. Ask the user for their roll number, e.g. 22CS045."
    }


def _unknown_drive(drive_id: int) -> dict:
    return {
        "error": "unknown_drive",
        "hint": f"No drive with id {drive_id}. Call list_open_drives to get valid ids."
    }


class PlacementTools:
    """Every method named in TOOL_NAMES is exposed to the model. Its docstring IS the prompt.

    Two tools are complete samples: check_eligibility (read-only) and apply_to_drive (side effect).
    Copy their patterns for the tools marked TODO.
    """

    READ_ONLY = ("list_open_drives", "get_student", "check_eligibility")
    SIDE_EFFECTS = ("apply_to_drive", "book_interview_slot", "notify_student")
    TOOL_NAMES = READ_ONLY + SIDE_EFFECTS

    def __init__(
        self,
        repo: InMemoryPlacementRepo,
        notifier,
        clock=lambda: datetime.now(timezone.utc)
    ):
        self.repo = repo
        self.notifier = notifier
        self.clock = clock

    def functions(self) -> dict:
        return {name: getattr(self, name) for name in self.TOOL_NAMES}

    def call(self, name: str, args: dict) -> dict:
        return dispatch(self.functions(), name, args)

    # ==================================================================
    # SAMPLE 1: read-only

    def _evaluate(self, s: Student, drive_id: int) -> list[dict]:
        failed = []

        for rule in self.repo.rules_for_drive(drive_id):
            actual = getattr(s, rule.field)

            if not _passes(rule, actual):
                failed.append({
                    "rule_id": rule.id,
                    "rule": str(rule),
                    "actual": actual
                })

        return failed

    def check_eligibility(self, student_id: str, drive_id: int) -> dict:
        """Decide whether ONE student may apply to ONE drive, using the drive's eligibility rules.

        Use before apply_to_drive, or when the user asks "can I apply", "am I eligible for
        <company>", or "why can't I apply". Do NOT use to find drives; use list_open_drives.
        Read-only: changes nothing.

        Args:
            student_id: Roll number, e.g. "22CS045".
            drive_id: Integer id returned by list_open_drives. Never a company name.

        Returns:
            {"student_id", "drive_id", "eligible", "failed_rules": [{"rule_id", "rule", "actual"}]}.
            Explain every failed rule to the user; do not invent rules that are not listed.
        """
        s = self.repo.get_student(student_id)

        if s is None:
            return _unknown_student(student_id)

        if self.repo.get_drive(drive_id) is None:
            return _unknown_drive(drive_id)

        failed = self._evaluate(s, drive_id)

        return {
            "student_id": s.roll_no,
            "drive_id": drive_id,
            "eligible": not failed,
            "failed_rules": failed
        }

    # ==================================================================
    # SAMPLE 2: side effect

    def apply_to_drive(self, student_id: str, drive_id: int) -> dict:
        """Submit a placement application for ONE student to ONE drive.

        Side effect: creates an application record the placement cell will act on. Call it only
        when the user clearly asks to apply or register ("apply me", "sign me up"), never to
        check or explore. Eligibility is re-checked here, but call check_eligibility first so
        you can explain the result.

        Args:
            student_id: Roll number, e.g. "22CS045".
            drive_id: Integer id returned by list_open_drives.

        Returns:
            {"application_id", "student_id", "drive_id", "status": "applied",
             "available_slots": [{"slot_id", "starts_at"}]}. Offer the slots to the user;
            book one only when they choose.
        """
        s = self.repo.get_student(student_id)

        if s is None:
            return _unknown_student(student_id)

        d = self.repo.get_drive(drive_id)

        if d is None:
            return _unknown_drive(drive_id)

        if d.status != "open" or d.deadline <= self.clock():
            return {
                "error": "drive_closed",
                "hint": f"{d.company} is not accepting applications. Call list_open_drives for open ones."
            }

        failed = self._evaluate(s, drive_id)

        if failed:
            return {
                "error": "not_eligible",
                "failed_rules": failed,
                "hint": "Explain the failed rules to the user. Do not retry."
            }

        try:
            application_id = self.repo.create_application(s.id, drive_id)

        except AlreadyApplied:
            return {
                "error": "already_applied",
                "hint": "The student has already applied to this drive. Tell the user; do not retry."
            }

        slots = self.repo.free_slots(drive_id)

        return {
            "application_id": application_id,
            "student_id": s.roll_no,
            "drive_id": drive_id,
            "status": "applied",
            "available_slots": [
                {
                    "slot_id": sl.id,
                    "starts_at": sl.starts_at.isoformat()
                }
                for sl in slots
            ]
        }

    # ==================================================================
    # YOUR TOOLS

    def get_student(self, student_id: str) -> dict:
        """Retrieve the profile of one student using their roll number.

        Use this tool when the user asks for their student details, profile information,
        branch, CGPA, backlog count, or graduation year. Do NOT use this tool to check
        eligibility for a placement drive; use check_eligibility for that.

        Read-only: this tool does not modify student or placement records.

        Args:
            student_id: The student's roll number, for example "22CS045".

        Returns:
            A dictionary containing student_id, name, branch, cgpa, backlogs, and grad_year.
            If the roll number does not exist, return an unknown_student error with a hint
            asking the user to provide a valid roll number.
        """
        s = self.repo.get_student(student_id)

        if s is None:
            return _unknown_student(student_id)

        return {
            "student_id": s.roll_no,
            "name": s.name,
            "branch": s.branch,
            "cgpa": s.cgpa,
            "backlogs": s.backlogs,
            "grad_year": s.grad_year,
        }

    def list_open_drives(
        self,
        branch: str | None = None,
        grad_year: int | None = None
    ) -> dict:
        """List currently open placement drives that match optional student filters.

        Use this tool when the user asks which companies, roles, or placement drives
        are currently available. It is also appropriate when the user wants open drives
        filtered by their branch or graduation year. Do NOT use this tool to decide whether
        one specific student is eligible for a specific drive; use check_eligibility instead.

        Read-only: this tool does not create applications, reserve interview slots, or change
        any placement records.

        Args:
            branch: Optional branch such as "CSE", "IT", "ECE", or "MECH". When provided,
                include only drives whose branch eligibility rule allows that branch.
            grad_year: Optional graduation year such as 2026. When provided, include only
                drives whose graduation-year eligibility rule allows that year.

        Returns:
            A dictionary containing "drives", ordered by soonest deadline first. Each drive
            contains drive_id, company, role, ctc_lpa, and deadline, where deadline is
            formatted as YYYY-MM-DD. Only currently open drives whose deadline is after
            the current tool clock are returned.
        """
        drives = self.repo.list_open_drives(self.clock())

        result = []

        for d in drives:

            if branch is not None:
                branch_rules = [
                    r
                    for r in self.repo.rules_for_drive(d.id)
                    if r.field == "branch"
                ]

                if branch_rules:
                    if any(
                        not _passes(rule, branch)
                        for rule in branch_rules
                    ):
                        continue

            if grad_year is not None:
                grad_rules = [
                    r
                    for r in self.repo.rules_for_drive(d.id)
                    if r.field == "grad_year"
                ]

                if grad_rules:
                    if any(
                        not _passes(rule, grad_year)
                        for rule in grad_rules
                    ):
                        continue

            result.append({
                "drive_id": d.id,
                "company": d.company,
                "role": d.role,
                "ctc_lpa": d.ctc_lpa,
                "deadline": d.deadline.strftime("%Y-%m-%d"),
            })

        return {"drives": result}

    def book_interview_slot(self, student_id: str, slot_id: int) -> dict:
        """Book one interview slot for a student who has already applied to its drive.

        Use this tool only when the student has selected a specific interview slot and
        clearly wants to book it. Do NOT use it merely to view available slots; those
        slots are returned by apply_to_drive.

        Side effect: claims an interview slot and changes placement scheduling state.
        Check that the student has an application for the slot's drive before attempting
        to claim the slot.

        Args:
            student_id: The student's roll number, for example "22CS045".
            slot_id: The integer interview-slot id selected by the student.

        Returns:
            On success, return slot_id, drive_id, starts_at in ISO-8601 format, and
            status "booked". If the student or slot is unknown, return the corresponding
            error. If the student has not applied to the slot's drive, return
            no_application. If another booking has already claimed the slot, return
            slot_taken and include the drive's remaining available_slots.
        """
        s = self.repo.get_student(student_id)

        if s is None:
            return _unknown_student(student_id)

        slot = self.repo.get_slot(slot_id)

        if slot is None:
            return {
                "error": "unknown_slot",
                "hint": f"No interview slot with id {slot_id}."
            }

        if not self.repo.has_application(s.id, slot.drive_id):
            return {
                "error": "no_application",
                "hint": "The student must apply to the drive before booking an interview slot."
            }

        if not self.repo.claim_slot(slot_id, s.id):
            available_slots = self.repo.free_slots(slot.drive_id)

            return {
                "error": "slot_taken",
                "available_slots": [
                    {
                        "slot_id": sl.id,
                        "starts_at": sl.starts_at.isoformat()
                    }
                    for sl in available_slots
                ]
            }

        return {
            "slot_id": slot.id,
            "drive_id": slot.drive_id,
            "starts_at": slot.starts_at.isoformat(),
            "status": "booked",
        }

    def notify_student(self, student_id: str, message: str) -> dict:
        """Send one notification to one student.

        Use this tool only when the user explicitly asks to notify, remind, or
        send a message to the student. The message must be 160 characters or
        fewer.

        Side effect: queues exactly one notification for the specified student.
        It does not modify placement applications or interview slots.

        Args:
            student_id: The student's roll number, for example "22CS045".
            message: The notification message to send. It must be at most
                160 characters long.

        Returns:
            On success, return the notification id, student id, message, and
            status "queued". If the student does not exist, return an
            unknown_student error. If the message is longer than 160 characters,
            return an invalid_message error and do not send anything.
        """
        # Check that the student exists.
        s = self.repo.get_student(student_id)

        if s is None:
            return _unknown_student(student_id)

        # Message must be 160 characters or fewer.
        if len(message) > 160:
            return {
                "error": "invalid_message",
                "hint": "Notification messages must be 160 characters or fewer.",
            }

        # Queue exactly one notification.
        notification_id = self.notifier.send(
            student_id,
            message,
        )

        return {
            "notification_id": notification_id,
            "student_id": student_id,
            "message": message,
            "status": "queued",
        }