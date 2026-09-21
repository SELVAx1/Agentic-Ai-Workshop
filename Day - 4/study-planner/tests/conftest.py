"""Shared pytest fixtures for the study planner tests."""
import pytest

from app.memory import RunStore
from app.planner_db import PlannerDb


class FakeClock:
    """A controllable clock for deterministic tests."""

    def __init__(self, start: float = 1_790_000_000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class SimulatedCrash(BaseException):
    """Like kill -9: nothing in the worker catches BaseException."""


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def db():
    """Fresh in-memory PlannerDb with seed data."""
    d = PlannerDb(":memory:")
    d.migrate()
    return d


@pytest.fixture
def store(clock):
    """Fresh in-memory RunStore backed by the controllable clock."""
    s = RunStore(":memory:", clock)
    s.migrate()
    return s
