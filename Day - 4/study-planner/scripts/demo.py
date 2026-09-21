"""Run the whole study planner end-to-end — no API key needed by default.

Usage:
    python -m scripts.demo            # scripted mock models, always same output
    python -m scripts.demo --real     # same two questions on real Gemini (~12 calls)
    python -m scripts.demo --crash    # kill worker after booking, resume, verify no duplicates

The --crash flag is the idempotency/crash-recovery proof required by the project brief.
It prints PASS if there is exactly one new booking and one notification after the replay.
"""
import argparse
import os
import tempfile

from scripts._term import BOLD, CYAN, DIM, GREEN, RED, RESET, print_step

# ── Demo questions ────────────────────────────────────────────────────────────
QUESTIONS = [
    (
        "22CS045",
        "Are there any Machine Learning slots available? "
        "Book the 10:00 slot on 2026-09-22 for me and send me a confirmation.",
    ),
    (
        "22IT017",
        "I already have 3 bookings today. Can I book another Data Structures slot on 2026-09-22?",
    ),
]


class Crash(BaseException):
    """Simulates kill -9: nothing in the worker catches BaseException."""


def _counts(db) -> str:
    return (
        f"bookings {db.count('booking')}   "
        f"notifications {db.count('notification')}   "
        f"idempotency keys {db.count('idempotency')}"
    )


def main() -> None:
    p = argparse.ArgumentParser(description="Study Planner Agent demo")
    p.add_argument("--real",  action="store_true", help="Use real Gemini instead of scripted mock")
    p.add_argument("--crash", action="store_true", help="Run the crash-and-replay idempotency demo")
    a = p.parse_args()

    # Use throwaway temp databases so every run is clean
    tmp = tempfile.mkdtemp(prefix="planner-demo-")
    os.environ["AGENT_DB"]   = os.path.join(tmp, "agent.db")
    os.environ["PLANNER_DB"] = os.path.join(tmp, "planner.db")

    from app.config import make_providers, open_stores
    from app.worker import Worker

    store, db = open_stores()
    providers = make_providers(mock=not a.real)

    print(f"{DIM}databases in {tmp}   model: {providers['supervisor'].model}{RESET}")
    print(f"{DIM}before: {_counts(db)}{RESET}\n")

    # Crash demo only runs the first question
    questions = QUESTIONS[:1] if a.crash else QUESTIONS

    for student_id, text in questions:
        thread = store.create_thread(student_id)
        run_id = store.enqueue(thread, text, providers["supervisor"].model)
        print(f"{CYAN}{student_id}>{RESET} {text}")

        if a.crash:
            # ── Crash scenario ─────────────────────────────────────────────
            # We hook db.once so that after book_slot commits its side effect
            # (seat taken, booking row written) the worker is killed before it
            # can record the idempotency key.  Worker-B then picks up the run
            # and must NOT create a second booking.
            real_once = db.once

            def once_then_die(key, tool_name, effect):
                result = real_once(key, tool_name, effect)
                if tool_name == "book_slot":
                    raise Crash()   # side effect committed; record NOT written
                return result

            db.once = once_then_die
            try:
                Worker(store, db, providers, worker_id="worker-A",
                       lease_seconds=60, on_step=print_step).run_once()
            except Crash:
                db.once = real_once
                print(f"\n  {RED}worker-A died right after writing the booking{RESET}")
                print(f"  {DIM}{_counts(db)}; run is '{store.get_run(run_id)['status']}'{RESET}")
                # Fast-forward the clock so the lease expires and worker-B can claim it
                store.clock = lambda: __import__("time").time() + 61
                print(f"  {DIM}...lease expires, worker-B claims the run{RESET}\n")

            Worker(store, db, providers, worker_id="worker-B",
                   lease_seconds=60, on_step=print_step).run_until_idle()
        else:
            Worker(store, db, providers, worker_id="demo-worker",
                   on_step=print_step).run_until_idle()

        run   = store.get_run(run_id)
        colour = GREEN if run["status"] == "succeeded" else RED
        if run["status"] == "succeeded":
            reply = store.load_history(thread)[-1]["text"]
        else:
            reply = run["error_code"] or run["status"]
        print(f"{colour}assistant>{RESET} {reply}")
        print(
            f"{DIM}run {run_id[:8]} {run['status']} after {run['attempts']} attempt(s), "
            f"{run['tokens_in']}+{run['tokens_out']} supervisor tokens{RESET}\n"
        )

    print(f"after:  {_counts(db)}")

    if a.crash:
        # seed has 1 booking (Divya on slot 3); Priya should add exactly 1 more
        ok = db.count("booking") == 2 and db.count("notification") == 1
        if ok:
            print(f"{GREEN}{BOLD}PASS: one new booking, one notification — no duplicates{RESET}")
        else:
            print(f"{RED}FAIL: unexpected counts — idempotency may be broken{RESET}")


if __name__ == "__main__":
    main()
