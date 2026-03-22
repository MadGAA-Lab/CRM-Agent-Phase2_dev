"""Tests for the dynamic per-task time budget manager."""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from time_budget import TimeBudget


# ── Helpers ──


def make_budget(total_min=100, reserve_min=10, total_tasks=10, cap_sec=300):
    """Create a TimeBudget with controlled env vars."""
    env = {
        "TIME_BUDGET_TOTAL_MIN": str(total_min),
        "TIME_BUDGET_RESERVE_MIN": str(reserve_min),
        "TIME_BUDGET_TOTAL_TASKS": str(total_tasks),
        "TIME_BUDGET_CAP_SEC": str(cap_sec),
    }
    orig = {k: os.environ.get(k) for k in env}
    os.environ.update(env)
    try:
        return TimeBudget()
    finally:
        for k, v in orig.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ── Tests ──


class TestTimeBudgetInit:
    def test_defaults_from_env(self):
        tb = make_budget(total_min=100, reserve_min=10, total_tasks=20, cap_sec=60)
        assert tb._budget_sec == (100 - 10) * 60  # 5400s
        assert tb._remaining_sec == 5400
        assert tb._remaining_tasks == 20
        assert tb.total_tasks == 20
        assert tb.cap_sec == 60

    def test_reserve_subtracted(self):
        tb = make_budget(total_min=50, reserve_min=50, total_tasks=10)
        assert tb._budget_sec == 0
        assert tb._remaining_sec == 0


class TestTaskTimeout:
    def test_even_split(self):
        tb = make_budget(total_min=100, reserve_min=0, total_tasks=10, cap_sec=9999)
        # 100 min = 6000s, 10 tasks → 600s each
        assert tb.task_timeout == pytest.approx(600.0)

    def test_capped_at_max(self):
        # 100 min = 6000s, 2 tasks → 3000s each, but cap=300
        tb = make_budget(total_min=100, reserve_min=0, total_tasks=2, cap_sec=300)
        assert tb.task_timeout == 300.0

    def test_floored_at_10s(self):
        # 1 min = 60s, 100 tasks → 0.6s each, but floor=10
        tb = make_budget(total_min=1, reserve_min=0, total_tasks=100, cap_sec=300)
        assert tb.task_timeout == 10.0

    def test_zero_remaining_tasks_returns_cap(self):
        tb = make_budget(total_min=100, reserve_min=0, total_tasks=1, cap_sec=120)
        tb._remaining_tasks = 0
        assert tb.task_timeout == 120.0


class TestStartEndTask:
    def test_start_returns_timeout(self):
        tb = make_budget(total_min=100, reserve_min=0, total_tasks=10, cap_sec=9999)
        timeout = tb.start_task()
        assert timeout == pytest.approx(600.0)
        assert tb._task_start is not None

    def test_end_decrements_counters(self):
        tb = make_budget(total_min=100, reserve_min=0, total_tasks=10, cap_sec=9999)
        tb.start_task()
        time.sleep(0.05)
        tb.end_task()

        assert tb._remaining_tasks == 9
        assert tb._remaining_sec < 6000.0
        assert tb._task_start is None

    def test_end_without_start_is_noop(self):
        tb = make_budget(total_min=100, reserve_min=0, total_tasks=10)
        tb.end_task()  # should not raise
        assert tb._remaining_tasks == 10

    def test_remaining_never_negative(self):
        tb = make_budget(total_min=0, reserve_min=0, total_tasks=2)
        tb._remaining_sec = 0.0
        tb.start_task()
        time.sleep(0.01)
        tb.end_task()

        assert tb._remaining_sec == 0.0
        assert tb._remaining_tasks == 1


class TestDynamicRebalancing:
    """Core behavior: fast tasks bank time for later tasks."""

    def test_fast_task_increases_later_budget(self):
        # 10 min = 600s, 3 tasks → 200s each
        tb = make_budget(total_min=10, reserve_min=0, total_tasks=3, cap_sec=9999)
        initial_timeout = tb.task_timeout
        assert initial_timeout == pytest.approx(200.0)

        # Simulate a fast task that takes ~0s
        tb.start_task()
        tb.end_task()  # effectively instant

        # Now 2 tasks left with ~600s remaining → ~300s each
        assert tb._remaining_tasks == 2
        assert tb.task_timeout > initial_timeout

    def test_slow_task_decreases_later_budget(self):
        # 10 min = 600s, 3 tasks → 200s each
        tb = make_budget(total_min=10, reserve_min=0, total_tasks=3, cap_sec=9999)

        # Simulate a slow task that burns 400s
        tb.start_task()
        tb._task_start = time.monotonic() - 400  # fake 400s elapsed
        tb.end_task()

        # 2 tasks left, ~200s remaining → ~100s each
        assert tb._remaining_tasks == 2
        assert tb.task_timeout == pytest.approx(100.0, abs=1.0)

    def test_multiple_tasks_sequential(self):
        # 5 min = 300s, 5 tasks → 60s each initially
        tb = make_budget(total_min=5, reserve_min=0, total_tasks=5, cap_sec=9999)

        # Task 1: instant
        tb.start_task()
        tb.end_task()
        assert tb._remaining_tasks == 4
        # ~300s / 4 = 75s
        assert tb.task_timeout == pytest.approx(75.0, abs=1.0)

        # Task 2: instant
        tb.start_task()
        tb.end_task()
        assert tb._remaining_tasks == 3
        # ~300s / 3 = 100s
        assert tb.task_timeout == pytest.approx(100.0, abs=1.0)


class TestExhausted:
    def test_not_exhausted_initially(self):
        tb = make_budget(total_min=100, reserve_min=0, total_tasks=10)
        assert not tb.exhausted

    def test_exhausted_when_zero(self):
        tb = make_budget(total_min=0, reserve_min=0, total_tasks=10)
        assert tb.exhausted

    def test_exhausted_after_draining(self):
        tb = make_budget(total_min=1, reserve_min=0, total_tasks=2, cap_sec=9999)
        tb.start_task()
        tb._task_start = time.monotonic() - 120  # fake 120s (> 60s budget)
        tb.end_task()
        assert tb.exhausted


class TestProductionDefaults:
    """Verify the actual competition defaults make sense."""

    def test_competition_defaults(self):
        tb = make_budget(total_min=4320, reserve_min=30, total_tasks=2140, cap_sec=300)
        # (4320-30)*60 = 257400s / 2140 ≈ 120.3s per task
        assert tb.task_timeout == pytest.approx(120.3, abs=0.5)
        assert tb.cap_sec == 300
        assert not tb.exhausted
