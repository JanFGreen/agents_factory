from agent_factory.estimate import DEFAULT_ROLE_COST, DEFAULT_ROUNDS_PER_TASK, estimate_plan
from agent_factory.state import Store


def test_estimate_uses_defaults_without_history_and_adds_ui_review_for_ui_tasks(tmp_path):
    store = Store(tmp_path / "s.db")
    tasks = [{"task_id": "T1", "file_scope": ["src/domain/a.ts"]}, {"task_id": "T2", "file_scope": ["src/ui/Card.tsx"]}]
    est = estimate_plan(store, "job-x", tasks, reserve_fraction=0.25)
    base = sum(DEFAULT_ROLE_COST[r] for r in ("builder", "code_reviewer", "security_reviewer", "product_evaluator", "test_reviewer"))
    assert est["per_task"][0]["usd"] == round(DEFAULT_ROUNDS_PER_TASK * base, 2)
    assert est["per_task"][1]["usd"] == round(DEFAULT_ROUNDS_PER_TASK * (base + DEFAULT_ROLE_COST["ui_reviewer"]), 2)
    assert est["basis"].startswith("defaults")
    assert est["suggested_limit_usd"] >= est["estimate_usd"] / 0.75
