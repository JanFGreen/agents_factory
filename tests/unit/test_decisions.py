import pytest

from agent_factory.config import load_config
from agent_factory.decisions import DecisionError, score, select, verify_ai_choice
from agent_factory.schemas import OptionsPayload

from ..conftest import make_factory_dir


@pytest.fixture
def sel(tmp_path):
    return load_config(make_factory_dir(tmp_path)).selection


def opts(*items, selected=None):
    return OptionsPayload.model_validate({"options": list(items), "selected_option_id": selected})


def opt(oid, r=(5, 5, 4, 4, 4), feasible=True, cost=1.0, minutes=30):
    names = ("goal_fit", "correctness", "maintainability", "delivery_time", "cost")
    return {"option_id": oid, "feasible": feasible, "ratings": dict(zip(names, r)) if feasible else None,
            "infeasible_reasons": [] if feasible else ["hard constraint"], "estimated_cost_usd": cost, "estimated_minutes": minutes}


def test_weighted_score_formula(sel):
    p = opts(opt("A", (5, 5, 5, 5, 5)))
    assert score(p.options[0], sel) == 1.0
    p = opts(opt("A", (4, 3, 2, 1, 0)))
    assert score(p.options[0], sel) == pytest.approx(0.3 * 0.8 + 0.25 * 0.6 + 0.2 * 0.4 + 0.15 * 0.2)


def test_af05_lower_score_choice_rejected(sel):
    p = opts(opt("A"), opt("B", (3, 3, 3, 3, 3)), selected="B")
    selection = select(p, sel, minimum_options=2)
    with pytest.raises(DecisionError, match="best feasible option is 'A'"):
        verify_ai_choice(p, selection)


def test_af05_duplicate_option_ids_rejected(sel):
    with pytest.raises(DecisionError, match="duplicate"):
        select(opts(opt("A"), opt("A")), sel, minimum_options=2)


def test_af46_tie_break_is_deterministic(sel):
    same = (4, 4, 4, 4, 4)
    p = opts(opt("B", same, cost=2), opt("A", same, cost=2), opt("C", same, cost=1))
    assert select(p, sel, minimum_options=2).selected_option_id == "C"  # cheaper wins after equal correctness/maintainability
    p = opts(opt("B", same), opt("A", same))
    first = select(p, sel, minimum_options=2)
    assert first.selected_option_id == "A" and first.near_tie
    assert all(select(p, sel, minimum_options=2).selected_option_id == "A" for _ in range(5))


def test_af04_no_feasible_option(sel):
    s = select(opts(opt("A", feasible=False), opt("B", feasible=False)), sel, minimum_options=2)
    assert s.selected_option_id is None and set(s.rejected) == {"A", "B"}


def test_minimum_options_for_nontrivial(sel):
    with pytest.raises(DecisionError, match="at least 2"):
        select(opts(opt("A")), sel, minimum_options=2)
    assert select(opts(opt("A")), sel, minimum_options=1).selected_option_id == "A"
