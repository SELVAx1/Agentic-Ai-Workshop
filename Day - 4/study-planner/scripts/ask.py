"""Queue a question from the command line and wait for the answer.

Usage:
    python -m scripts.ask "Are there any Machine Learning slots on 2026-09-22?" --student 22CS045

The worker must be running in a separate terminal for the answer to appear:
    python -m scripts.worker
"""
import argparse
import time

from app.config import GEMINI_MODEL, open_stores
from scripts._term import CYAN, DIM, GREEN, RED, RESET


def main() -> None:
    p = argparse.ArgumentParser(description="Queue a study-planner question and wait for the reply")
    p.add_argument("text",              help="The student's question")
    p.add_argument("--student", default="22CS045",
                   help="Student ID (default: 22CS045)")
    p.add_argument("--thread",  default=None,
                   help="Existing thread ID to continue a conversation")
    a = p.parse_args()

    store, _ = open_stores()
    thread = a.thread or store.create_thread(a.student)
    run_id = store.enqueue(thread, a.text, GEMINI_MODEL)

    print(f"{DIM}thread {thread}{RESET}")
    print(f"{CYAN}run {run_id}{RESET} queued — waiting for a worker...")

    while True:
        run = store.get_run(run_id)
        if run["status"] in ("succeeded", "failed", "cancelled", "dead"):
            break
        time.sleep(0.5)

    if run["status"] == "succeeded":
        reply = store.load_history(thread)[-1]["text"]
        print(f"{GREEN}assistant>{RESET} {reply}")
    else:
        print(f"{RED}run ended: {run['status']} ({run.get('error_code', '?')}){RESET}")


if __name__ == "__main__":
    main()
