"""Idempotency key helpers. Nothing domain-specific here — copied as-is from the library assistant."""
import hashlib
import json
from datetime import date


def _normalise(value):
    """Recursively sort dicts so key order doesn't change the hash."""
    if isinstance(value, dict):
        return {k: _normalise(v) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return [_normalise(v) for v in value]
    return value


def canonical_json(value) -> str:
    return json.dumps(_normalise(value), sort_keys=True, separators=(",", ":"))


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def idempotency_key(run_id: str, step_seq: int, tool_name: str, args: dict) -> str:
    """A key that is stable across retries but unique per (run, step, tool, args)."""
    payload = canonical_json({"run": run_id, "seq": step_seq, "tool": tool_name, "args": args})
    return f"{run_id[:8]}-s{step_seq}-{tool_name}-{_sha256(payload)}"


def notification_dedupe_key(student_id: str, message: str, day: date) -> str:
    """Same message to the same student on the same day → same key, so it's sent only once."""
    payload = canonical_json({"student": student_id, "msg": message, "day": str(day)})
    return f"notif-{_sha256(payload)}"
