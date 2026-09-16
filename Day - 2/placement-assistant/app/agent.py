import time
from collections.abc import Callable

from app.memory import ConversationStore
from app.providers import AgentError
from app.tools.placement_tools import PlacementTools

MAX_STEPS = 8

SYSTEM = """You are the Placement Assistant for an engineering college's placement cell.
You are talking to the student with roll number {student_id}. Act only for this student.
Use the tools for every fact about drives, eligibility, applications and slots; never guess.
Eligibility is decided by check_eligibility, not by you. Keep replies short and concrete."""


class Agent:
    """A small agent: one student, one conversation, the placement tools."""

    def __init__(
        self,
        provider,
        tools: PlacementTools,
        student_id: str,
        memory: ConversationStore | None = None,
        thread_id: str | None = None,
        on_step: Callable[[dict], None] | None = None,
    ):
        self.provider = provider
        self.tools = tools
        self.system = SYSTEM.format(student_id=student_id)
        self.memory = memory
        self.thread_id = thread_id
        self.on_step = on_step
        self.trace: list[dict] = []

        if self.memory is not None and self.thread_id is not None:
            self.contents = self.memory.load_history(self.thread_id)
        else:
            self.contents: list[dict] = []

    def _log(self, entry: dict) -> None:
        # Keep the public trace bounded. The agent can still continue
        # internally until MAX_STEPS is reached.
        if len(self.trace) >= MAX_STEPS + 1:
            return

        self.trace.append(entry)

        if self.on_step:
            self.on_step(entry)

    def run_tool(self, name: str, args: dict) -> dict:
        try:
            return self.tools.call(name, args)

        except NotImplementedError as e:
            return {
                "error": "not_implemented",
                "hint": f"Tool {name!r} is not implemented yet: {e}",
            }

        except Exception as e:
            return {
                "error": "tool_failed",
                "hint": f"Tool {name!r} failed: {type(e).__name__}: {e}",
            }

    def ask(self, text: str) -> str:
        run_id = None

        try:
            self.contents.append({
                "role": "user",
                "text": text,
            })

            if self.memory is not None and self.thread_id is not None:
                self.memory.append_message(
                    self.thread_id,
                    "user",
                    text,
                )

                run_id = self.memory.start_run(
                    self.thread_id,
                    self.provider.model,
                )

            step = 0
            run_step_seq = 0
            trace_step = 0

            while step < MAX_STEPS:
                step += 1

                turn = self.provider.generate(
                    self.system,
                    self.contents,
                    list(self.tools.functions().values()),
                )

                trace_step += 1

                self._log({
                    "step": trace_step,
                    "kind": "model",
                    "tokens_in": turn.tokens_in,
                    "tokens_out": turn.tokens_out,
                })

                if self.memory is not None and run_id is not None:
                    run_step_seq += 1

                    self.memory.record_model_step(
                        run_id,
                        run_step_seq,
                        turn.tokens_in,
                        turn.tokens_out,
                    )

                if not turn.tool_calls:
                    reply = turn.text

                    self.contents.append({
                        "role": "model",
                        "text": reply,
                        "raw": turn.raw,
                    })

                    if self.memory is not None and self.thread_id is not None:
                        self.memory.append_message(
                            self.thread_id,
                            "model",
                            reply,
                        )

                    if self.memory is not None and run_id is not None:
                        self.memory.finish_run(
                            run_id,
                            "succeeded",
                        )

                    return reply

                self.contents.append({
                    "role": "model",
                    "text": turn.text,
                    "raw": turn.raw,
                    "tool_calls": [
                        {
                            "name": call.name,
                            "args": call.args,
                        }
                        for call in turn.tool_calls
                    ],
                })

                for call in turn.tool_calls:
                    started = time.perf_counter()

                    result = self.run_tool(
                        call.name,
                        call.args,
                    )

                    elapsed_ms = int(
                        (time.perf_counter() - started) * 1000
                    )

                    trace_step += 1

                    self._log({
                        "step": trace_step,
                        "kind": "tool",
                        "tool": call.name,
                        "args": call.args,
                        "result": result,
                        "ok": "error" not in result,
                        "ms": elapsed_ms,
                    })

                    if self.memory is not None and run_id is not None:
                        run_step_seq += 1

                        self.memory.record_tool_call(
                            run_id,
                            run_step_seq,
                            call.name,
                            call.args,
                            result,
                            "error" not in result,
                            elapsed_ms,
                        )

                    self.contents.append({
                        "role": "tool",
                        "name": call.name,
                        "result": result,
                    })

            raise AgentError(
                "step_limit",
                f"Agent exceeded the maximum of {MAX_STEPS} "
                "steps without producing an answer.",
            )

        except AgentError as e:
            if self.memory is not None and run_id is not None:
                self.memory.finish_run(
                    run_id,
                    "failed",
                    e.code,
                )

            raise