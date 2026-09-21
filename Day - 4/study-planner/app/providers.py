"""Model providers. The agent only knows `generate`.

Includes:
  - GeminiProvider  : real Google Gemini (needs GEMINI_API_KEY)
  - ScriptedProvider: replays a fixed list of turns in order (used by tests)
  - PositionalMock  : resumes from the right position after a crash (used by crash demo)
  - RoutedMock      : picks a script by phrase, answers by position (used by demo)
  - demo_providers(): scripted mock providers for the two demo questions
"""
from dataclasses import dataclass, field
from typing import Any


class AgentError(Exception):
    """A run could not finish. `retryable` says whether trying again later could work."""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.message, self.retryable = code, message, retryable


@dataclass
class ToolCall:
    name: str
    args: dict


@dataclass
class ModelTurn:
    text: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    raw: Any = None   # provider-native content (kept for Gemini's thought signatures)


# contents list shape understood by every provider:
#   {"role": "user",  "text": str}
#   {"role": "model", "text": str | None, "tool_calls": [{"name", "args"}], "raw": ...}
#   {"role": "tool",  "name": str, "result": dict}


class GeminiProvider:
    """Calls the real Google Gemini API. Reads GEMINI_API_KEY from the environment."""

    def __init__(self, model: str):
        from google import genai
        self.client = genai.Client()
        self.model = model

    def _to_gemini(self, contents: list[dict]):
        from google.genai import types
        out: list = []
        for c in contents:
            if c["role"] == "user":
                out.append(types.Content(role="user", parts=[types.Part.from_text(text=c["text"])]))
            elif c["role"] == "model":
                if c.get("raw") is not None:
                    out.append(c["raw"])
                    continue
                parts = [types.Part.from_text(text=c["text"])] if c.get("text") else []
                parts += [types.Part.from_function_call(name=t["name"], args=t["args"])
                          for t in c.get("tool_calls", [])]
                out.append(types.Content(role="model", parts=parts))
            elif c["role"] == "tool":
                part = types.Part.from_function_response(name=c["name"], response=c["result"])
                if out and out[-1].role == "user" and all(p.function_response for p in out[-1].parts):
                    out[-1].parts.append(part)
                else:
                    out.append(types.Content(role="user", parts=[part]))
        return out

    def generate(self, system: str, contents: list[dict], tools: list) -> ModelTurn:
        from google.genai import errors, types
        config = types.GenerateContentConfig(
            system_instruction=system, tools=tools, temperature=0,
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True))
        try:
            resp = self.client.models.generate_content(
                model=self.model, contents=self._to_gemini(contents), config=config)
        except errors.APIError as e:
            if e.code == 429:
                raise AgentError("provider_rate_limited", "Model quota exhausted.", True) from e
            if e.code and e.code >= 500:
                raise AgentError("provider_unavailable", "Model provider failed.", True) from e
            raise AgentError("provider_error", str(e), False) from e

        content = resp.candidates[0].content if resp.candidates else None
        parts = (content.parts or []) if content else []
        text = "".join(p.text for p in parts if p.text and not p.thought) or None
        calls = [ToolCall(fc.name, dict(fc.args or {})) for fc in (resp.function_calls or [])]
        usage = resp.usage_metadata
        return ModelTurn(text=text, tool_calls=calls,
                         tokens_in=(usage.prompt_token_count or 0) if usage else 0,
                         tokens_out=(usage.candidates_token_count or 0) if usage else 0,
                         raw=content)


class ScriptedProvider:
    """Replays a fixed list of turns in call order. No network, no quota."""

    model = "mock"

    def __init__(self, script: list, loop: bool = False):
        self.original, self.script, self.loop = list(script), list(script), loop
        self.calls: list[list[dict]] = []

    def generate(self, system: str, contents: list[dict], tools: list) -> ModelTurn:
        self.calls.append([dict(c) for c in contents])
        if not self.script and self.loop:
            self.script = list(self.original)
        if not self.script:
            return ModelTurn(text="(mock) script exhausted")
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


class PositionalMock:
    """Answers by position in the current turn, not by call count.
    A resumed run gets the NEXT un-answered turn, not the first one.
    `slow` sleeps before each answer so you have time to kill the worker mid-run."""

    model = "mock"

    def __init__(self, turns: list[ModelTurn], slow: float = 0.0):
        self.turns, self.slow = turns, slow
        self.calls: list[list[dict]] = []

    def generate(self, system: str, contents: list[dict], tools: list) -> ModelTurn:
        import time
        self.calls.append([dict(c) for c in contents])
        last_user = max(i for i, c in enumerate(contents) if c["role"] == "user")
        position = sum(1 for c in contents[last_user:] if c["role"] == "model")
        if self.slow:
            time.sleep(self.slow)
        if position >= len(self.turns):
            return ModelTurn(text="(mock) nothing more to do.")
        return self.turns[position]


class RoutedMock:
    """Several scripted conversations in one mock: picks a script by phrase, answers by position."""

    model = "mock"

    def __init__(self, routes: dict[str, list[ModelTurn]], slow: float = 0.0):
        self.routes, self.slow = routes, slow
        self.calls: list[list[dict]] = []

    def generate(self, system: str, contents: list[dict], tools: list) -> ModelTurn:
        import time
        self.calls.append([dict(c) for c in contents])
        last_user = max(i for i, c in enumerate(contents) if c["role"] == "user")
        request = contents[last_user]["text"]
        position = sum(1 for c in contents[last_user:] if c["role"] == "model")
        if self.slow:
            time.sleep(self.slow)
        for phrase, turns in self.routes.items():
            if phrase.lower() in request.lower():
                return turns[position] if position < len(turns) else ModelTurn(text="(mock) done.")
        return ModelTurn(text="(mock) I have no script for that request.")


# ──────────────────────────────────────────────────────────────────────────────
# Scripted demo providers
# ──────────────────────────────────────────────────────────────────────────────

def _call(name, **args):
    """Shorthand: a model turn that calls one tool with the given args."""
    return ModelTurn(text=None, tool_calls=[ToolCall(name, args)], tokens_in=100, tokens_out=10)


def demo_providers(slow: float = 0.0) -> dict:
    """Scripted mock providers covering the two demo questions.

    Demo question 1 (22CS045):
        "Are there any Machine Learning slots available? Book the 10:00 slot on 2026-09-22 for me."
        Flow: supervisor → ask_schedule (list ML slots) → ask_desk (check+book+notify)

    Demo question 2 (22IT017):
        "I already have 3 bookings today. Can I book another Data Structures slot?"
        Flow: supervisor → ask_schedule (find DS slots) → ask_desk (check_can_book → blocked)
    """
    return {
        "supervisor": RoutedMock({
            # ── question 1 ──────────────────────────────────────────────────
            "Machine Learning": [
                _call("ask_schedule",
                      question="List all available study slots for Machine Learning."),
                _call("ask_desk",
                      request="Book slot 4 (Machine Learning, 2026-09-22 10:00) for the student and send a confirmation."),
                ModelTurn(text="(mock) Great news: slot 4 for Machine Learning on 2026-09-22 at 10:00 is now booked, and a confirmation has been sent to you."),
            ],
            # ── question 2 ──────────────────────────────────────────────────
            "Data Structures": [
                _call("ask_schedule",
                      question="Find Data Structures study slots."),
                _call("ask_desk",
                      request="Check if the student can book slot 1 (Data Structures, 2026-09-22 09:00)."),
                ModelTurn(text="(mock) Sorry, you already have 3 bookings on 2026-09-22, which is the daily limit. You cannot book another slot on that day."),
            ],
        }, slow),

        "schedule": RoutedMock({
            "Machine Learning": [
                _call("list_subjects", text="Machine Learning"),
                _call("get_subject_slots", subject_id=2),
                ModelTurn(text="(mock) Machine Learning (CS401) has slots: slot 4 on 2026-09-22 at 10:00 (25 seats), slot 5 on 2026-09-23 at 10:00 (23 seats)."),
            ],
            "Data Structures": [
                _call("list_subjects", text="Data Structures"),
                _call("get_subject_slots", subject_id=1),
                ModelTurn(text="(mock) Data Structures (CS301) has slots: slot 1 on 2026-09-22 at 09:00 (30 seats), slot 2 on 2026-09-22 at 14:00 (30 seats), slot 3 on 2026-09-23 at 09:00 (28 seats)."),
            ],
        }, slow),

        "desk": RoutedMock({
            "Book slot 4": [
                _call("check_can_book", slot_id=4),
                ModelTurn(text=None, tool_calls=[
                    ToolCall("book_slot", {"slot_id": 4}),
                    ToolCall("notify_student", {"message": "Your booking for Machine Learning on 2026-09-22 at 10:00 is confirmed."})],
                    tokens_in=150, tokens_out=25),
                ModelTurn(text="(mock) Booked slot 4 and sent a confirmation to the student."),
            ],
            "slot 1": [
                _call("check_can_book", slot_id=1),
                ModelTurn(text="(mock) Cannot book: student already has 3 bookings on 2026-09-22, the daily policy limit."),
            ],
        }, slow),
    }
