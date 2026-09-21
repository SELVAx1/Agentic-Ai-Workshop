"""Terminal colour helpers shared by all scripts."""
import os

# Disable colours when not connected to a real terminal (CI, file redirect, etc.)
_NO_COLOUR = not os.isatty(1) if hasattr(os, "isatty") else True

CYAN  = "\033[96m"  if not _NO_COLOUR else ""
GREEN = "\033[92m"  if not _NO_COLOUR else ""
RED   = "\033[91m"  if not _NO_COLOUR else ""
DIM   = "\033[2m"   if not _NO_COLOUR else ""
BOLD  = "\033[1m"   if not _NO_COLOUR else ""
RESET = "\033[0m"   if not _NO_COLOUR else ""


def print_step(step: dict) -> None:
    """Pretty-print one agent step to stdout."""
    agent = step.get("agent", "?")
    kind  = step.get("kind",  "?")

    if kind == "delegate":
        tool = step.get("tool", "?")
        args = step.get("args", {})
        arg_str = list(args.values())[0][:60] if args else ""
        print(f"  {DIM}[{agent}] → {tool}: {arg_str!r}{RESET}")

    elif kind == "tool":
        tool     = step.get("tool", "?")
        ok       = step.get("ok", True)
        replayed = step.get("replayed", False)
        ms       = step.get("ms", 0)
        marker   = GREEN + "✓" if ok else RED + "✗"
        replay   = f" {DIM}(replayed){RESET}" if replayed else ""
        print(f"    {marker}{RESET} {DIM}{agent}/{tool}{RESET}  {DIM}{ms}ms{replay}{RESET}")

    elif kind == "model":
        text       = step.get("text") or ""
        tool_calls = step.get("tool_calls") or []
        if tool_calls:
            names = ", ".join(c["name"] for c in tool_calls)
            print(f"  {DIM}[{agent}] calls: {names}{RESET}")
        elif text:
            short = text[:80].replace("\n", " ")
            print(f"  {DIM}[{agent}] {short}{RESET}")
