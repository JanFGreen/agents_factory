"""Validated data contracts shared by the controller, skills and adapters."""

from __future__ import annotations

import json
import re
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

Verdict = Literal["PASS", "CHANGES", "BLOCKED", "UNKNOWN"]
Severity = Literal["low", "medium", "high", "critical"]
FindingStatus = Literal["open", "fixed", "disputed", "accepted-risk"]
Risk = Literal["low", "medium", "high"]
FutureStatus = Literal["required_now", "committed_next", "hypothetical"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Location(Model):
    path: str = Field(max_length=500)
    line: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _relative(self) -> "Location":
        if self.path.startswith("/") or ".." in self.path.split("/"):
            raise ValueError(f"location path must be repo-relative: {self.path!r}")
        return self


class Finding(Model):
    id: str = Field(pattern=r"^[A-Za-z0-9_.-]{1,40}$")
    severity: Severity
    blocking: bool
    requirement_ids: list[str] = []
    location: Location | None = None
    problem: str = Field(min_length=1, max_length=4000)
    evidence_ids: list[str] = []
    expected_fix: str | None = Field(default=None, max_length=4000)
    status: FindingStatus = "open"


class AgentEnvelope(Model):
    """Common structured output every skill run must return."""

    schema_version: Literal["1"]
    kind: str = Field(max_length=60)
    verdict: Verdict
    summary: str = Field(max_length=8000)
    findings: list[Finding] = []
    checked_criteria: list[str] = []
    limitations: list[str] = []
    payload: dict[str, Any] = {}

    @model_validator(mode="after")
    def _pass_has_no_open_blockers(self) -> "AgentEnvelope":
        if self.verdict == "PASS":
            open_blockers = [f.id for f in self.findings if f.blocking and f.status == "open"]
            if open_blockers:
                raise ValueError(f"PASS with open blocking findings {open_blockers} violates the contract")
        return self


# --- skill payloads -------------------------------------------------------------------


class DimensionScore(Model):
    score: int | None = Field(ge=0, le=2)
    evidence: str = ""


class ClassificationPayload(Model):
    dimensions: dict[str, DimensionScore]
    risk: Risk
    risk_reasons: list[str] = []
    single_atomic_task: bool
    known_pattern: bool
    new_trust_boundary: bool = False
    forced_complex_reasons: list[str] = []


class FutureNeed(Model):
    description: str
    status: FutureStatus


class IntakePayload(Model):
    goal: str
    acceptance_criteria: list[str] = []
    constraints: list[str] = []
    open_questions: list[str] = []
    future_directions: list[FutureNeed] = []


class OptionRatings(Model):
    goal_fit: float = Field(ge=0, le=5)
    correctness: float = Field(ge=0, le=5)
    maintainability: float = Field(ge=0, le=5)
    delivery_time: float = Field(ge=0, le=5)
    cost: float = Field(ge=0, le=5)


class Option(Model):
    option_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    title: str = ""
    feasible: bool
    infeasible_reasons: list[str] = []
    ratings: OptionRatings | None = None
    estimated_cost_usd: float | None = None
    estimated_minutes: float | None = None


class OptionsPayload(Model):
    options: list[Option]
    selected_option_id: str | None = None
    spike_requested: str | None = None


class Requirement(Model):
    id: str = Field(pattern=r"^REQ-[A-Za-z0-9_-]+$")
    text: str
    source: str = "original_request"


class IntentLink(Model):
    intent: str
    requirement_ids: list[str]


class AcceptanceCriterion(Model):
    id: str
    requirement_ids: list[str]
    observable: str


class SpecPayload(Model):
    micro_spec: dict[str, Any] | None = None
    requirements: list[Requirement]
    intent_map: list[IntentLink]
    acceptance_criteria: list[AcceptanceCriterion]
    files: dict[str, str] = {}


class SpecEvaluationPayload(Model):
    dimensions: dict[str, Literal["PASS", "CHANGES"]]
    counterexample: str
    uncovered_requirements: list[str] = []


class TaskSpec(Model):
    task_id: str = Field(pattern=r"^TASK-[A-Za-z0-9_-]+$")
    requirement_ids: list[str]
    depends_on: list[str] = []
    inputs: list[str] = []
    outputs: list[str] = []
    file_scope: list[str] = []
    risk: Risk = "low"
    estimated_minutes: float = 30
    acceptance: str
    verification: list[str] = []
    parallel_safe_with: list[str] = []
    future_constraints: list[str] = []
    parent: str | None = None


class PlanPayload(Model):
    tasks: list[TaskSpec]
    future_needs: list[FutureNeed] = []


class TaskReview(Model):
    task_id: str
    verdict: Verdict
    notes: str = ""


class DecompositionPayload(Model):
    requirements_map: dict[str, list[str]]
    per_task: list[TaskReview] = []
    dag_ok: bool


class ImplementPayload(Model):
    changed_files: list[str] = []
    tests_added: list[str] = []
    handoff: str = ""
    claimed_checks: list[str] = []


PAYLOADS: dict[str, type[Model]] = {
    "intake": IntakePayload,
    "classify-feature": ClassificationPayload,
    "options-review": OptionsPayload,
    "select-option": OptionsPayload,
    "spec-author": SpecPayload,
    "spec-evaluate": SpecEvaluationPayload,
    "plan": PlanPayload,
    "decomposition-review": DecompositionPayload,
    "implement": ImplementPayload,
}


class ContractError(ValueError):
    pass


_JSON_BLOCK = re.compile(r"```json\s*\n(.*?)\n```", re.DOTALL)


def extract_json_object(text: str) -> dict[str, Any]:
    """Take the last fenced ```json block, or the whole text if it is a bare object."""
    blocks = _JSON_BLOCK.findall(text)
    candidate = blocks[-1] if blocks else text.strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ContractError(f"output is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ContractError("output JSON must be an object")
    return value


def parse_envelope(raw: dict[str, Any] | str, expected_kind: str) -> tuple[AgentEnvelope, Model | None]:
    if isinstance(raw, str):
        raw = extract_json_object(raw)
    try:
        envelope = AgentEnvelope.model_validate(raw)
    except ValidationError as exc:
        raise ContractError(str(exc)) from exc
    if envelope.kind != expected_kind:
        raise ContractError(f"expected kind {expected_kind!r}, got {envelope.kind!r}")
    payload_model = PAYLOADS.get(expected_kind)
    payload = None
    if payload_model is not None and envelope.verdict in ("PASS", "CHANGES"):
        try:
            payload = payload_model.model_validate(envelope.payload)
        except ValidationError as exc:
            raise ContractError(f"{expected_kind} payload: {exc}") from exc
    return envelope, payload


def envelope_json_schema() -> dict[str, Any]:
    return AgentEnvelope.model_json_schema()
