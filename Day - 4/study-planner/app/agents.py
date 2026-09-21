"""Three agents for the study planner.

    student ──▶ supervisor ──ask_schedule──▶ schedule agent  (list_subjects, get_subject_slots, get_slot)
                            └─ask_desk─────▶ desk agent      (get_student_schedule, check_can_book,
                                                               book_slot, cancel_slot, notify_student)

The supervisor talks to the student and delegates all work to the two specialists.
To the supervisor, each specialist is just a tool ("agent as tool" pattern).
"""
import time
from collections.abc import Callable

from app.idempotency import idempotency_key
from app.planner_db import PlannerDb
from app.providers import AgentError
from app.tools.planner_tools import DeskTools, ScheduleTools, Toolset

SPECIALIST_MAX_STEPS = 8

# ──────────────────────────────────────────────────────────────────────────────
# System prompts
# ──────────────────────────────────────────────────────────────────────────────

SUPERVISOR_SYSTEM = """You are the Student Study Planner Assistant, helping student {student_id}.
You never look up subjects/slots or change bookings yourself — always delegate:
- ask_schedule: finding subjects, listing available study slots, checking seat counts.
- ask_desk: anything about this student's bookings, checking eligibility, booking or cancelling slots.
Give each specialist a complete, specific request that includes slot IDs once you know them.
Answer the student briefly, using only what the specialists reported."""

SCHEDULE_SYSTEM = """You are the schedule specialist for the student study planner.
Find subjects and study slots, and report their IDs, times, and seat availability.
You cannot book or cancel anything. Be brief and precise."""

DESK_SYSTEM = """You are the booking desk specialist, acting for student {student_id} only.
Always call check_can_book before book_slot. Never decide policy yourself: report the reasons
the tools give. Confirm a successful booking with notify_student. Report what you did, briefly."""


# ──────────────────────────────────────────────────────────────────────────────
# Shared agent loop helpers
# ──────────────────────────────────────────────────────────────────────────────

def run_tool(toolset: Toolset, db: PlannerDb, key: str, name: str, args: dict) -> tuple[dict, bool]:
    """Run one tool call for any agent. Returns (result, replayed). Never raises, except AgentError.

    Side effects run at most once per key (idempotency). Delegations pass the key down, so
    the specialist's own side effects get keys derived from it — a replayed delegation
    replays its side effects safely too.
    """
    try:
        if name in toolset.DELEGATES:
            return toolset.delegate(name, args, key), False
        if name in toolset.SIDE_EFFECTS:
            result, fresh = db.once(key, name, lambda: toolset.call(name, args))
            return result, not fresh
        return toolset.call(name, args), False
    except AgentError:
        raise
    except NotImplementedError:
        return {"error": "not_implemented",
                "hint": f"{name} is not available yet."}, False
    except Exception as e:
        return {"error": "tool_failed",
                "hint": f"{name} failed ({type(e).__name__}). Try another way or tell the student."}, False


def run_specialist(agent: str, system: str, toolset: Toolset, *, db: PlannerDb,
                   provider, task: str, parent_key: str,
                   on_step: Callable[[dict], None] | None = None) -> dict:
    """A specialist's whole agent loop, called inside one supervisor tool call."""
    contents = [{"role": "user", "text": task}]
    functions = list(toolset.functions().values())
    used = []
    seq = 0
    while seq < SPECIALIST_MAX_STEPS:
        turn = provider.generate(system, contents, functions)
        seq += 1
        if not turn.tool_calls:
            return {"agent": agent, "answer": turn.text or "", "tools_used": used}
        contents.append({
            "role": "model", "text": turn.text, "raw": turn.raw,
            "tool_calls": [{"name": c.name, "args": c.args} for c in turn.tool_calls]
        })
        for call in turn.tool_calls:
            seq += 1
            key = idempotency_key(parent_key, seq, call.name, call.args)
            started = time.perf_counter()
            result, replayed = run_tool(toolset, db, key, call.name, call.args)
            used.append(call.name)
            if on_step:
                on_step({"agent": agent, "kind": "tool", "tool": call.name, "args": call.args,
                         "result": result, "ok": "error" not in result, "replayed": replayed,
                         "ms": round((time.perf_counter() - started) * 1000)})
            contents.append({"role": "tool", "name": call.name, "result": result})
    return {"agent": agent, "error": "specialist_step_limit", "tools_used": used,
            "hint": "The specialist could not finish. Ask the student to simplify the request."}


# ──────────────────────────────────────────────────────────────────────────────
# Supervisor toolset (delegation only)
# ──────────────────────────────────────────────────────────────────────────────

class SupervisorTools(Toolset):
    """The supervisor's only tools are the two specialist delegates."""

    TOOL_NAMES = ("ask_schedule", "ask_desk")
    DELEGATES = ("ask_schedule", "ask_desk")

    def __init__(self, db: PlannerDb, providers: dict, student_id: str, on_step=None):
        self.db = db
        self.providers = providers
        self.student_id = student_id
        self.on_step = on_step

    def ask_schedule(self, question: str) -> dict:
        """Ask the schedule specialist to find subjects or available study slots.

        Use for "what subjects are there", "is there a Data Structures slot on Monday",
        "how many seats in slot 4". It cannot book or cancel anything.

        Args:
            question: A complete request, e.g. "List all CS slots on 2026-09-22."

        Returns:
            {"agent": "schedule", "answer": str, "tools_used": [str]}.
        """
        raise RuntimeError("delegations run through delegate()")

    def ask_desk(self, request: str) -> dict:
        """Ask the booking desk specialist to act on the student's bookings. CAN CHANGE DATA:
        book slots, cancel slots, and send notifications.

        Use for booking, cancelling, "what have I booked", and confirmations. Include the
        slot_id from the schedule specialist when booking.

        Args:
            request: A complete instruction, e.g. "Book slot 4 for the student and confirm."

        Returns:
            {"agent": "desk", "answer": str, "tools_used": [str]}.
        """
        raise RuntimeError("delegations run through delegate()")

    def delegate(self, name: str, args: dict, key: str) -> dict:
        bad = self._call_check(name, args)
        if bad:
            return bad
        if self.on_step:
            self.on_step({"agent": "supervisor", "kind": "delegate", "tool": name, "args": args})
        if name == "ask_schedule":
            return run_specialist(
                "schedule", SCHEDULE_SYSTEM, ScheduleTools(self.db),
                db=self.db, provider=self.providers["schedule"],
                task=args["question"], parent_key=key, on_step=self.on_step)
        return run_specialist(
            "desk", DESK_SYSTEM.format(student_id=self.student_id), DeskTools(self.db, self.student_id),
            db=self.db, provider=self.providers["desk"],
            task=args["request"], parent_key=key, on_step=self.on_step)

    def _call_check(self, name: str, args: dict) -> dict | None:
        field = "question" if name == "ask_schedule" else "request"
        if set(args) != {field} or not isinstance(args[field], str) or not args[field].strip():
            return {"error": "invalid_arguments",
                    "hint": f"{name} takes one non-empty string: {field}."}
        return None
