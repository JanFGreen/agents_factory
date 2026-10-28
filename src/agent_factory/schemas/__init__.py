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


class DecisionQuestion(Model):
    """A product decision the request leaves open; the operator answers it in guided mode."""

    id: str = Field(pattern=r"^Q-\d{1,2}$")
    question: str = Field(min_length=1, max_length=500)
    why: str = Field(min_length=1, max_length=500)  # what is built differently depending on the answer
    options: list[str] = Field(default=[], max_length=5)
    proposed: str = Field(min_length=1, max_length=500)  # the default when the operator accepts or in auto mode


class IntakePayload(Model):
    goal: str
    acceptance_criteria: list[str] = []
    constraints: list[str] = []
    open_questions: list[str] = []
    decisions: list[DecisionQuestion] = []
    future_directions: list[FutureNeed] = []

    @model_validator(mode="after")
    def _unique_decisions(self) -> "IntakePayload":
        ids = [d.id for d in self.decisions]
        if len(ids) != len(set(ids)):
            raise ValueError("decision ids must be unique")
        return self


class OptionRatings(Model):
    goal_fit: float = Field(ge=0, le=5)
    correctness: float = Field(ge=0, le=5)
    maintainability: float = Field(ge=0, le=5)
    delivery_time: float = Field(ge=0, le=5)
    cost: float = Field(ge=0, le=5)


class Option(Model):
    option_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    title: str = ""
    description: str = ""
    risks: list[str] = []
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
    difficulty: Literal["easy", "medium", "hard"] | None = None  # the planner's label; the controller may raise it
    difficulty_reasons: list[str] = []


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


CheckStatus = Literal["PASS", "FAIL", "NOT_APPLICABLE", "NOT_RUN"]
HEURISTICS = ("H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H9", "H10")  # Nielsen's ten usability heuristics
VISUAL_ASPECTS = ("hierarchy", "layout", "typography", "color", "consistency", "polish")


class UiViewport(Model):
    name: str
    width: int = Field(ge=200, le=4000)
    evidence_ids: list[str] = []


class HeuristicCheck(Model):
    id: Literal["H1", "H2", "H3", "H4", "H5", "H6", "H7", "H8", "H9", "H10"]
    status: CheckStatus
    evidence_ids: list[str] = []
    note: str = Field(default="", max_length=1000)


class VisualScore(Model):
    """Visual quality of the touched screens judged from screenshots: 1 (broken) .. 5 (polished)."""

    aspect: Literal["hierarchy", "layout", "typography", "color", "consistency", "polish"]
    score: int | None = Field(default=None, ge=1, le=5)  # None: not rendered (local-checkout)
    evidence_ids: list[str] = []
    note: str = Field(default="", max_length=1000)


class DesignDirectionCheck(Model):
    """Conformance of the touched screens to the repository's design direction and reference images."""

    status: CheckStatus = "NOT_APPLICABLE"
    references: list[str] = []  # reference images compared, repo-relative
    deviations: list[str] = []


class UserFlow(Model):
    """A task the user repeats, walked in the served app: every action counted, every screen shot."""

    name: str = Field(min_length=1, max_length=200)  # e.g. "first run on a phone", "play the next round"
    steps: list[str] = Field(min_length=1)  # one entry per user action, in order
    actions: int = Field(ge=1)  # clicks, taps and key presses the flow needs
    status: Literal["PASS", "FAIL"]
    evidence_ids: list[str] = Field(min_length=1)
    note: str = Field(default="", max_length=1000)


class LayoutShift(Model):
    """Measured in the browser: the main element's position before and after a state change."""

    transition: str = Field(min_length=1, max_length=200)  # e.g. "Start round pressed"
    element: str = Field(min_length=1, max_length=200)  # e.g. "the board"
    shift_px: float = Field(ge=0)
    evidence_ids: list[str] = []


class FirstScreen(Model):
    """What a phone shows before any scrolling (360x640): the primary action and every choice it needs."""

    viewport: str = "360x640"
    primary_action_visible: bool
    required_choices_visible: bool
    evidence_ids: list[str] = Field(min_length=1)
    note: str = Field(default="", max_length=1000)


class InteractionState(Model):
    """One control in one state, with contrast measured from computed colors in the page."""

    control: str = Field(min_length=1, max_length=200)
    state: Literal["default", "hover", "focus", "checked", "checked+hover", "checked+focus", "disabled", "pressed"]
    text_contrast: float | None = Field(default=None, ge=1, le=21)
    indicator_contrast: float | None = Field(default=None, ge=1, le=21)  # focus ring or selection mark vs. its surroundings
    evidence_ids: list[str] = []


class UiReviewPayload(Model):
    viewports: list[UiViewport] = []
    states: dict[str, CheckStatus] = {}
    a11y: dict[str, CheckStatus] = {}
    consistency_notes: list[str] = []
    heuristics: list[HeuristicCheck] = []
    visual: list[VisualScore] = []
    design_direction: DesignDirectionCheck = DesignDirectionCheck()
    # False when the served app does not show the touched screens yet (e.g. components a later task mounts):
    # the screenshots then prove what the app shows, and the visual judgement waits for the task that mounts them.
    touched_screens_rendered: bool = True
    not_rendered_reason: str = Field(default="", max_length=1000)
    # Feature walkthrough (mode: feature): measured, not judged by eye.
    flows: list[UserFlow] = []
    first_screen: FirstScreen | None = None
    layout_shifts: list[LayoutShift] = []
    interaction_states: list[InteractionState] = []

    @model_validator(mode="after")
    def _unique(self) -> "UiReviewPayload":
        for name, ids in (("heuristics", [h.id for h in self.heuristics]), ("visual", [v.aspect for v in self.visual])):
            if len(ids) != len(set(ids)):
                raise ValueError(f"{name} entries must be unique")
        if not self.touched_screens_rendered and not self.not_rendered_reason.strip():
            raise ValueError("touched_screens_rendered: false needs not_rendered_reason")
        return self


FAILURE_SCENARIOS = ("timeout", "cancellation", "retry_idempotent", "lost_response", "double_submit", "unmount_or_navigation",
                     "partial_failure")
ScenarioStatus = Literal["PASS", "FAIL", "NOT_APPLICABLE"]


class FailureModeCheck(Model):
    """What happens to one effectful operation when the world misbehaves; each answer cites code or a test."""

    path: str = Field(max_length=500)
    operation: str = Field(min_length=1, max_length=300)  # e.g. "POST /api/results via saveResult()"
    scenarios: dict[Literal["timeout", "cancellation", "retry_idempotent", "lost_response", "double_submit",
                            "unmount_or_navigation", "partial_failure"], ScenarioStatus]
    evidence: str = Field(min_length=1, max_length=3000)  # file:line or test name per scenario, the reason for NOT_APPLICABLE
    probe: str | None = Field(default=None, max_length=300)  # probe test file in the probe workspace that shows a FAIL
    finding_id: str | None = None  # the finding that reports a FAIL

    @model_validator(mode="after")
    def _complete(self) -> "FailureModeCheck":
        missing = [s for s in FAILURE_SCENARIOS if s not in self.scenarios]
        if missing:
            raise ValueError(f"failure scenarios not assessed for {self.path}: {missing}")
        if "FAIL" in self.scenarios.values() and not self.finding_id:
            raise ValueError(f"a FAIL scenario for {self.path} needs the finding_id that reports it")
        return self


class CodeReviewPayload(Model):
    reviewed_head_sha: str = ""
    diff_stats: dict[str, int] = {}
    requirements_checked: list[str] = []
    scope_creep: list[str] = []
    reclassification_signal: dict[str, Any] = {}
    failure_modes: list[FailureModeCheck] = []


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
    "ui-review": UiReviewPayload,
    "code-review": CodeReviewPayload,
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


def envelope_json_schema(kind: str | None = None) -> dict[str, Any]:
    """Envelope schema; with ``kind``, ``kind`` is pinned and ``payload`` carries that skill's exact schema.

    Handing the runtime the full payload schema lets it constrain the output up front instead
    of the controller rejecting extra or mistyped fields after the fact (a repair round-trip).
    BLOCKED/UNKNOWN envelopes may still carry an empty payload.
    """
    schema = AgentEnvelope.model_json_schema()
    if kind is None:
        return schema
    schema["properties"]["kind"] = {"const": kind, "type": "string"}
    payload_model = PAYLOADS.get(kind)
    if payload_model is not None:
        payload_schema = payload_model.model_json_schema(ref_template="#/$defs/{model}")
        defs = schema.setdefault("$defs", {})
        defs.update(payload_schema.pop("$defs", {}))
        defs[payload_model.__name__] = payload_schema
        schema["properties"]["payload"] = {"anyOf": [
            {"$ref": f"#/$defs/{payload_model.__name__}"},
            {"type": "object", "maxProperties": 0},
        ]}
    return schema
