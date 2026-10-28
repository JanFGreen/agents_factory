import time

import pytest

from agent_factory import analysis, budgets
from agent_factory.config import load_config
from agent_factory.scheduler import LeaseLost, acquire_lease, acquire_slot, check_fence, NoSlot, release_allocation, wait_for_slot
from agent_factory.state import ConcurrentModification, Store

from ..conftest import make_factory_dir


@pytest.fixture
def env(tmp_path):
    cfg = load_config(make_factory_dir(tmp_path))
    store = Store(tmp_path / "state.db")
    with store.tx() as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, feature_id, repo_id, source_id, scope_revision, state, repo_path, analysis_rounds_limit,"
            " budget_limit_usd, created_at, updated_at) VALUES ('job-000000000001','F','r','s',1,'INTAKE','/x',3,10,'t','t')"
        )
    return cfg, store


def test_af28_reservations_stop_new_parallel_starts(env):
    cfg, store = env
    job = "job-000000000001"
    ids = [budgets.reserve(store, cfg.factory.budget, job, "build", 2.5) for _ in range(3)]  # 7.5 = 75% build share
    with pytest.raises(budgets.BudgetExhausted):
        budgets.reserve(store, cfg.factory.budget, job, "build", 0.1)
    # the verification reserve is still available for reviews
    budgets.reserve(store, cfg.factory.budget, job, "review", 2.0)
    with pytest.raises(budgets.BudgetExhausted):
        budgets.reserve(store, cfg.factory.budget, job, "review", 1.0)
    budgets.settle(store, job, ids[0], cost_usd=0.5, model_id="m", input_tokens=1, output_tokens=1, attempt_id=None, source="t")
    assert budgets.status(store, job).spent == 0.5


def test_af26_unknown_cost_charges_full_reservation(env):
    cfg, store = env
    job = "job-000000000001"
    rid = budgets.reserve(store, cfg.factory.budget, job, "build")
    budgets.settle(store, job, rid, cost_usd=None, model_id="unknown-model", input_tokens=None, output_tokens=None, attempt_id=None, source="t")
    st = budgets.status(store, job)
    assert st.spent == cfg.factory.budget.max_cost_per_agent_call and st.unknown_cost_calls == 1


def test_budget_raise_requires_reason_and_operator(env):
    cfg, store = env
    with pytest.raises(ValueError):
        budgets.raise_budget(store, "job-000000000001", 20, "")
    budgets.raise_budget(store, "job-000000000001", 20, "bigger feature")
    assert store.job("job-000000000001")["budget_limit_usd"] == 20


def test_af20_only_one_lease_holder(env):
    _, store = env
    a = acquire_lease(store, "job:TASK-01:builder", "ctl-a", 60)
    assert a is not None and acquire_lease(store, "job:TASK-01:builder", "ctl-b", 60) is None


def test_af21_stale_epoch_result_rejected(env):
    _, store = env
    old = acquire_lease(store, "k", "ctl-a", 0.01)
    time.sleep(0.05)
    new = acquire_lease(store, "k", "ctl-b", 60)
    assert new.epoch == old.epoch + 1
    with pytest.raises(LeaseLost):
        with store.tx() as conn:
            check_fence(conn, old)


def test_live_pid_keeps_expired_lease(env):
    import os
    _, store = env
    acquire_lease(store, "k2", "ctl-a", 0.01, pid=os.getpid())
    time.sleep(0.05)
    assert acquire_lease(store, "k2", "ctl-b", 60) is None


def test_global_slots(env):
    _, store = env
    slots = [acquire_slot(store, "agent_process", 2, "j", "o") for _ in range(2)]
    with pytest.raises(NoSlot):
        acquire_slot(store, "agent_process", 2, "j", "o")
    release_allocation(store, slots[0])
    acquire_slot(store, "agent_process", 2, "j", "o")


def test_slots_of_a_dead_controller_are_reclaimed(env):
    """Live finding: two slots held by controllers killed the day before starved the next job's lanes."""
    import subprocess
    import sys

    _, store = env
    dead = subprocess.Popen([sys.executable, "-c", "pass"])
    dead.wait()
    for _ in range(2):
        acquire_slot(store, "agent_process", 3, "old-job", f"controller-{dead.pid}-abc123")
    acquire_slot(store, "agent_process", 3, "j", "test-owner")  # not a controller id: never reclaimed
    taken = [acquire_slot(store, "agent_process", 3, "j", "o") for _ in range(2)]
    assert len(taken) == 2
    reclaimed = store.all("SELECT owner FROM resource_allocations WHERE owner LIKE '%:reclaimed'")
    assert len(reclaimed) == 2
    with pytest.raises(NoSlot):
        acquire_slot(store, "agent_process", 3, "j", "o")


def test_waiting_for_a_slot_gets_one_when_released_and_times_out_otherwise(env):
    import threading

    _, store = env
    held = acquire_slot(store, "agent_process", 1, "j", "o")
    threading.Timer(0.3, release_allocation, (store, held)).start()
    assert wait_for_slot(store, "agent_process", 1, "j", "o", timeout=5, poll=0.05)
    with pytest.raises(NoSlot):
        wait_for_slot(store, "agent_process", 1, "j", "o", timeout=0.2, poll=0.05)


def test_cas_detects_concurrent_writer(env):
    _, store = env
    v = store.job("job-000000000001")["version"]
    with store.tx() as conn:
        store.cas_update_job(conn, "job-000000000001", v, risk="low")
    with pytest.raises(ConcurrentModification):
        with store.tx() as conn:
            store.cas_update_job(conn, "job-000000000001", v, risk="high")


def test_round_counter_is_shared_and_bounded(env):
    _, store = env
    job = "job-000000000001"
    for n in range(3):
        rs = analysis.start_round(store, job)
        assert rs.current_number == n + 1
        assert analysis.start_round(store, job).current_number == n + 1  # idempotent on resume
        analysis.end_round(store, job, "CHANGES", ["same finding"])
    with pytest.raises(analysis.AnalysisLimitReached):
        analysis.start_round(store, job)
    assert analysis.consecutive_rounds_without_progress(store, job) == 2
    analysis.raise_limit(store, job, 4, "operator decided one more round")
    assert analysis.start_round(store, job).current_number == 4  # history kept, not reset
