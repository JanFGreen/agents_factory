"""Mechanics of the eval runner with the scripted runtime (says nothing about model quality)."""

from pathlib import Path

from agent_factory.adapters.fake import FakeRuntime
from agent_factory.config import load_config
from agent_factory.evals import run_suite

from ..conftest import ROOT, UNIT_CHECK, base_responders, make_factory_dir, write_checks


def test_eval_runner_scores_with_hidden_golden_tests(tmp_path):
    d = make_factory_dir(tmp_path)
    write_checks(d, [UNIT_CHECK])
    cfg = load_config(d)
    responders = base_responders("trivial")
    report = run_suite(cfg, ROOT / "tests" / "evals" / "dev.yaml", "full",
                       runtime_factory=lambda _: FakeRuntime(dict(responders)), task_ids=["E01-simple-fix", "E04-farewell"])
    by_id = {r["task_id"]: r for r in report["results"]}
    assert by_id["E01-simple-fix"]["success"]  # scripted builder implements exactly E01
    assert not by_id["E04-farewell"]["success"] and by_id["E04-farewell"]["accepted_by_factory"]
    assert report["false_accepts"] == 1  # the fake reviewers accepted a wrong product: measured, not hidden
    assert Path(report["report_path"]).exists()


def test_suite_has_at_least_20_tasks_across_categories():
    import yaml
    suite = yaml.safe_load((ROOT / "tests" / "evals" / "dev.yaml").read_text())
    assert len(suite["tasks"]) >= 20
    assert {"simple-fix", "crud", "api", "ui", "auth", "regression", "migration", "incomplete"} <= {t["category"] for t in suite["tasks"]}
