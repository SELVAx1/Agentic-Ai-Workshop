"""Stable fingerprints for side effects. Hashing, used for exactly-once."""
import hashlib
import json
import re
from datetime import date


def _canonical(value):
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_canonical(v) for v in value]
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def canonical_json(value) -> str:
    """One spelling per meaning: sort keys, strip insignificant whitespace, normalize numbers."""
    normalized = _canonical(value)
    return json.dumps(normalized, separators=(",", ":"), sort_keys=True, ensure_ascii=False)


def idempotency_key(run_id: str, step_seq: int, tool_name: str, args: dict) -> str:
    """A stable hash for one tool call in one run step."""
    payload = canonical_json([run_id, step_seq, tool_name, args])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def notification_dedupe_key(roll_no: str, message: str, day: date) -> str:
    """Deduplicate same notification to same student on same day by normalized text."""
    normal = re.sub(r"\s+", " ", message.strip())
    payload = canonical_json([roll_no, normal, day.isoformat()])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
