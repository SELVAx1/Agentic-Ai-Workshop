"""Supervisor delegation, specialist loops, and agent-as-tool pattern."""
from app.agents import SupervisorTools, run_specialist, run_tool
from app.providers import ModelTurn, ScriptedProvider, ToolCall, demo_providers
from app.tools.planner_tools import ScheduleTools


def test_supervisor_only_has_delegation_tools(db):
    tools = SupervisorTools(db, demo_providers(), "22CS045")
    assert set(tools.functions()) == {"ask_schedule", "ask_desk"}
    assert set(tools.DELEGATES) == set(tools.TOOL_NAMES)


def test_schedule_specialist_finds_subjects(db):
    result, replayed = run_tool(
        SupervisorTools(db, demo_providers(), "22CS045"), db, "k1",
        "ask_schedule", {"question": "List all available study slots for Machine Learning."})
    assert result["agent"] == "schedule"
    assert "list_subjects" in result["tools_used"]
    assert not replayed


def test_desk_specialist_books_and_notifies(db):
    result, _ = run_tool(
        SupervisorTools(db, demo_providers(), "22CS045"), db, "k1",
        "ask_desk",
        {"request": "Book slot 4 (Machine Learning, 2026-09-22 10:00) for the student and send a confirmation."})
    assert "book_slot" in result["tools_used"]
    assert "notify_student" in result["tools_used"]
    assert db.count("booking") == 2        # seed 1 + new booking
    assert db.count("notification") == 1


def test_repeated_delegation_with_same_key_does_not_duplicate(db):
    tools = SupervisorTools(db, demo_providers(), "22CS045")
    args  = {"request": "Book slot 4 (Machine Learning, 2026-09-22 10:00) for the student and send a confirmation."}
    run_tool(tools, db, "same-key", "ask_desk", args)
    run_tool(tools, db, "same-key", "ask_desk", args)
    assert db.count("booking") == 2        # still only one new booking
    assert db.count("notification") == 1
    assert db.count("idempotency") == 2    # two idempotency keys for the two side-effects


def test_bad_delegation_args_are_returned_as_error(db):
    result, _ = run_tool(
        SupervisorTools(db, demo_providers(), "22CS045"), db, "k",
        "ask_desk", {"request": ""})
    assert result["error"] == "invalid_arguments"


def test_looping_specialist_stops_at_step_limit(db):
    looping = ScriptedProvider(
        [ModelTurn(text=None, tool_calls=[ToolCall("list_subjects", {"text": "CS"})])],
        loop=True)
    result = run_specialist(
        "schedule", "sys", ScheduleTools(db),
        db=db, provider=looping, task="find subjects", parent_key="k")
    assert result["error"] == "specialist_step_limit"


def test_schedule_specialist_sees_only_its_own_task(db):
    providers = demo_providers()
    run_tool(
        SupervisorTools(db, providers, "22CS045"), db, "k",
        "ask_schedule", {"question": "List all available study slots for Machine Learning."})
    seen = providers["schedule"].calls[0]
    assert seen == [{"role": "user", "text": "List all available study slots for Machine Learning."}]
    assert providers["desk"].calls == []
