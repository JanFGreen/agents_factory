import pytest

from agent_factory.config import load_config
from agent_factory.features import classify, normalize
from agent_factory.schemas import ClassificationPayload

from ..conftest import make_factory_dir

DIMS = ("scope", "coupling", "state_and_data", "interfaces", "uncertainty", "verification")


@pytest.fixture
def wf(tmp_path):
    return load_config(make_factory_dir(tmp_path)).workflow


def payload(scores, risk="low", atomic=True, known=True, forced=None, boundary=False):
    return ClassificationPayload.model_validate({
        "dimensions": {d: {"score": s, "evidence": "e"} for d, s in zip(DIMS, scores)},
        "risk": risk, "single_atomic_task": atomic, "known_pattern": known, "new_trust_boundary": boundary,
        "forced_complex_reasons": forced or [],
    })


def test_af48_label_fix_is_trivial(wf):
    c = classify(payload([0, 0, 0, 0, 0, 1]), wf, request_text="Fix the typo in the footer label")
    assert c.path == "trivial" and c.score == 1 and c.risk == "low"


def test_af49_one_line_auth_change_is_not_fast_path(wf):
    c = classify(payload([0, 0, 0, 0, 0, 0]), wf, request_text="Allow login without password for admins")
    assert c.risk == "high" and c.path == "standard"
    assert any("raised" in r for r in c.reasons)


def test_unknown_dimension_never_trivial(wf):
    c = classify(payload([0, 0, 0, 0, None, 0]), wf, request_text="Tweak greeting")
    assert c.score is None and c.path == "standard"


def test_thresholds(wf):
    assert classify(payload([1, 1, 1, 1, 1, 1]), wf, request_text="x").path == "standard"
    assert classify(payload([2, 2, 2, 1, 0, 0]), wf, request_text="x").path == "complex"
    assert classify(payload([0, 0, 0, 0, 0, 0], forced=["data migration"]), wf, request_text="x").path == "complex"
    assert classify(payload([0, 0, 0, 0, 0, 0], boundary=True), wf, request_text="x").path == "standard"


def test_af47_feature_normalisation_keeps_original():
    f = normalize("Dodaj historię operacji", source_kind="cli", repo_id="app")
    assert f.original_request == "Dodaj historię operacji" and f.goal == f.original_request
    assert f.feature_id.startswith("FEAT-") and f.repo_id == "app"
    g = normalize({"feature_id": "FEAT-042", "original_request": "x", "acceptance_criteria": ["a"]}, source_kind="file", repo_id="app")
    assert g.feature_id == "FEAT-042" and g.acceptance_criteria == ["a"]
    with pytest.raises(ValueError):
        normalize({"original_request": "x", "repo_id": "other"}, source_kind="file", repo_id="app")
