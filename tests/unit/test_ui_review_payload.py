"""The UI review payload: heuristics, visual scores and design conformance from screenshots."""

import pytest

from agent_factory.adapters.fake import envelope
from agent_factory.schemas import HEURISTICS, VISUAL_ASPECTS, ContractError, UiReviewPayload, envelope_json_schema, parse_envelope


def test_an_empty_payload_is_valid_and_the_design_check_defaults_to_not_applicable():
    payload = UiReviewPayload.model_validate({})
    assert payload.design_direction.status == "NOT_APPLICABLE" and payload.heuristics == [] and payload.visual == []


def test_scores_heuristic_ids_and_duplicates_are_checked():
    for bad in ({"visual": [{"aspect": "layout", "score": 6}]},
                {"visual": [{"aspect": "beauty", "score": 3}]},
                {"heuristics": [{"id": "H11", "status": "PASS"}]},
                {"heuristics": [{"id": "H1", "status": "PASS"}, {"id": "H1", "status": "FAIL"}]}):
        with pytest.raises(ContractError):
            parse_envelope(envelope("ui-review", payload=bad), "ui-review")


def test_the_runtime_schema_carries_the_ui_review_payload():
    schema = envelope_json_schema("ui-review")
    assert "UiReviewPayload" in schema["$defs"]
    assert len(HEURISTICS) == 10 and len(VISUAL_ASPECTS) == 6
