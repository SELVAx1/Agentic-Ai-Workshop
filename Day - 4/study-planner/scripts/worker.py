"""Long-running worker process — run this in a dedicated terminal.

Usage:
    python -m scripts.worker           # uses mock model (no API key)
    python -m scripts.worker --real    # uses real Gemini

Queue questions with:
    python -m scripts.ask "your question" --student 22CS045
"""
import argparse
import logging

from scripts._term import BOLD, DIM, GREEN, RESET, print_step

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")


def main() -> None:
    p = argparse.ArgumentParser(description="Study Planner background worker")
    p.add_argument("--real", action="store_true", help="Use real Gemini instead of mock")
    p.add_argument("--poll", type=float, default=1.0,
                   help="Seconds between queue polls when idle (default: 1)")
    a = p.parse_args()

    from app.config import make_providers, open_stores
    from app.worker import Worker

    store, db = open_stores()
    providers = make_providers(mock=not a.real)

    print(f"{BOLD}Study Planner Worker{RESET}  model: {providers['supervisor'].model}")
    print(f"{DIM}Polling every {a.poll}s. Press Ctrl-C to stop.{RESET}\n")

    worker = Worker(store, db, providers, on_step=print_step)
    try:
        worker.run_forever(poll_seconds=a.poll)
    except KeyboardInterrupt:
        print(f"\n{GREEN}Worker stopped.{RESET}")


if __name__ == "__main__":
    main()
