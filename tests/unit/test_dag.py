import pytest

from agent_factory.config import load_config
from agent_factory.scheduler import topological_order, validate_dag
from agent_factory.schemas import TaskSpec

from ..conftest import make_factory_dir


@pytest.fixture
def dcfg(tmp_path):
    return load_config(make_factory_dir(tmp_path)).workflow.decomposition


def t(tid, reqs, deps=(), files=(), parallel=(), minutes=30, parent=None):
    return TaskSpec(task_id=tid, requirement_ids=list(reqs), depends_on=list(deps), file_scope=list(files),
                    parallel_safe_with=list(parallel), acceptance="a", estimated_minutes=minutes, parent=parent)


def test_valid_dag_orders_contract_first(dcfg):
    r = validate_dag([t("TASK-02", ["REQ-1"], ["TASK-01"]), t("TASK-01", ["REQ-2"])], ["REQ-1", "REQ-2"], dcfg)
    assert r.ok and r.order == ["TASK-01", "TASK-02"]


def test_af06_cycle_and_missing_dependency(dcfg):
    r = validate_dag([t("TASK-01", ["REQ-1"], ["TASK-02"]), t("TASK-02", ["REQ-1"], ["TASK-01", "TASK-09"])], ["REQ-1"], dcfg)
    assert "dependency cycle detected" in r.problems
    assert any("missing task TASK-09" in p for p in r.problems)


def test_af51_uncovered_requirement_and_duplicate(dcfg):
    r = validate_dag([t("TASK-01", ["REQ-1"]), t("TASK-01", ["REQ-1"])], ["REQ-1", "REQ-2"], dcfg)
    assert any("duplicate" in p for p in r.problems) and any("REQ-2" in p for p in r.problems)


def test_af52_oversized_leaf_and_depth(dcfg):
    r = validate_dag([t("TASK-01", ["REQ-1"], minutes=500)], ["REQ-1"], dcfg)
    assert any("too large" in p for p in r.problems)
    chain = [t("TASK-01", ["REQ-1"]), t("TASK-02", ["REQ-1"], parent="TASK-01"), t("TASK-03", ["REQ-1"], parent="TASK-02"), t("TASK-04", ["REQ-1"], parent="TASK-03")]
    assert any("max depth" in p for p in validate_dag(chain, ["REQ-1"], dcfg).problems)


def test_parallel_tasks_cannot_share_files(dcfg):
    r = validate_dag([t("TASK-01", ["REQ-1"], files=["src/a.py"], parallel=["TASK-02"]), t("TASK-02", ["REQ-1"], files=["src/a.py"])], ["REQ-1"], dcfg)
    assert any("share files" in p for p in r.problems)


def test_topological_order_none_on_cycle():
    assert topological_order([t("TASK-01", ["R"], ["TASK-02"]), t("TASK-02", ["R"], ["TASK-01"])]) is None
