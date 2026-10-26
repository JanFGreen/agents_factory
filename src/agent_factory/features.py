"""Feature input normalisation and complexity/risk classification (blueprint §7.1–7.2).

The LLM scores six dimensions with evidence; this code sums them and decides the
path. Complexity and risk are separate, and risk has a deterministic floor that the
classifier cannot lower.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import WorkflowConfig
from .identities import digest
from .policy import max_risk
from .schemas import ClassificationPayload, FutureNeed
from .state import Store, new_id, now

Path_ = Literal["trivial", "standard", "complex"]
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


class FeatureFile(BaseModel):
    """What a user may submit. Only ``original_request`` is mandatory."""

    model_config = ConfigDict(extra="forbid")

    feature_id: str | None = Field(default=None, pattern=_ID.pattern)
    repo_id: str | None = Field(default=None, pattern=_ID.pattern)
    original_request: str = Field(min_length=1, max_length=20000)
    goal: str | None = Field(default=None, max_length=4000)
    constraints: list[str] = []
    acceptance_criteria: list[str] = []
    future_directions: list[FutureNeed] = []


@dataclass(frozen=True)
class NormalizedFeature:
    feature_id: str
    repo_id: str
    source_kind: str
    source_id: str
    original_request: str
    goal: str
    constraints: list[str]
    acceptance_criteria: list[str]
    future_directions: list[dict[str, str]]
    source_snapshot_sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "feature_id": self.feature_id,
            "repo_id": self.repo_id,
            "source_kind": self.source_kind,
            "source_id": self.source_id,
            "original_request": self.original_request,
            "goal": self.goal,
            "constraints": self.constraints,
            "acceptance_criteria": self.acceptance_criteria,
            "future_directions": self.future_directions,
            "source_snapshot_sha256": self.source_snapshot_sha256,
        }

    def scope_digest(self) -> str:
        return digest({k: v for k, v in self.as_dict().items() if k != "source_snapshot_sha256"})


def normalize(raw: dict[str, Any] | str, *, source_kind: str, repo_id: str, source_id: str | None = None) -> NormalizedFeature:
    snapshot = raw if isinstance(raw, str) else json.dumps(raw, sort_keys=True, ensure_ascii=False)
    parsed = FeatureFile(original_request=raw) if isinstance(raw, str) else FeatureFile.model_validate(raw)
    if parsed.repo_id and parsed.repo_id != repo_id:
        raise ValueError(f"feature repo_id {parsed.repo_id!r} does not match the selected project {repo_id!r}")
    snapshot_sha = digest(snapshot.encode("utf-8"))
    feature_id = parsed.feature_id or f"FEAT-{snapshot_sha[:8]}"
    return NormalizedFeature(
        feature_id=feature_id,
        repo_id=repo_id,
        source_kind=source_kind,
        source_id=source_id or f"{source_kind}:{feature_id}",
        original_request=parsed.original_request,
        goal=parsed.goal or parsed.original_request,
        constraints=list(parsed.constraints),
        acceptance_criteria=list(parsed.acceptance_criteria),
        future_directions=[f.model_dump() for f in parsed.future_directions],
        source_snapshot_sha256=snapshot_sha,
    )


def store_feature(store: Store, feature: NormalizedFeature) -> int:
    """Persist the immutable snapshot; a changed request creates a new revision."""
    with store.tx() as conn:
        row = conn.execute(
            "SELECT revision, source_snapshot_sha256 FROM features WHERE feature_id = ? ORDER BY revision DESC LIMIT 1",
            (feature.feature_id,),
        ).fetchone()
        if row is not None and row["source_snapshot_sha256"] == feature.source_snapshot_sha256:
            return row["revision"]
        revision = 1 if row is None else row["revision"] + 1
        conn.execute(
            "INSERT INTO features VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                feature.feature_id, revision, feature.repo_id, feature.source_kind, feature.source_id,
                feature.original_request, feature.goal, json.dumps(feature.as_dict()),
                feature.source_snapshot_sha256, now(),
            ),
        )
        store.event(conn, None, "feature.received", {"feature_id": feature.feature_id, "revision": revision})
        return revision


def load_feature(store: Store, feature_id: str, revision: int) -> dict[str, Any]:
    row = store.one("SELECT normalized_json FROM features WHERE feature_id = ? AND revision = ?", (feature_id, revision))
    if row is None:
        raise KeyError(f"unknown feature {feature_id} r{revision}")
    return json.loads(row["normalized_json"])


# --- risk floor ------------------------------------------------------------------------

_RISK_FLOOR_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("high", re.compile(r"\b(auth\w*|login|logowan\w*|password|hasł\w*|permission\w*|uprawnie\w*|role|token|session|sesj\w*|tenant\w*|oauth|sso)\b", re.I), "authentication/authorization"),
    ("high", re.compile(r"\b(payment\w*|płatnoś\w*|billing|invoice|faktur\w*)\b", re.I), "payments"),
    ("high", re.compile(r"\b(migrat\w*|migracj\w*|drop table|delete all|usuń wszystk\w*)\b", re.I), "data migration/destruction"),
    ("medium", re.compile(r"\b(webhook\w*|api|endpoint\w*|upload\w*|export\w*|eksport\w*|import\w*)\b", re.I), "external interface/data movement"),
    ("medium", re.compile(r"\b(personal data|dane osobow\w*|pii|gdpr|rodo)\b", re.I), "personal data"),
]


def request_risk_floor(text: str) -> tuple[str, list[str]]:
    risk, reasons = "low", []
    for level, pattern, label in _RISK_FLOOR_RULES:
        if pattern.search(text):
            risk = max_risk(risk, level)
            reasons.append(f"request mentions {label}")
    return risk, reasons


# --- classification ------------------------------------------------------------------


@dataclass
class Classification:
    path: Path_
    score: int | None
    risk: str
    reasons: list[str] = field(default_factory=list)
    dimensions: dict[str, Any] = field(default_factory=dict)

    def digest(self) -> str:
        return digest({"path": self.path, "score": self.score, "risk": self.risk, "dimensions": self.dimensions})


def classify(payload: ClassificationPayload, cfg: WorkflowConfig, *, request_text: str, risk_floor: str = "low") -> Classification:
    cx = cfg.complexity
    reasons: list[str] = []
    dims = {name: payload.dimensions.get(name) for name in cx.dimensions}
    missing = [name for name, value in dims.items() if value is None]
    unknown = [name for name, value in dims.items() if value is not None and value.score is None]
    if missing:
        reasons.append(f"dimensions not assessed: {missing}")
    if unknown:
        reasons.append(f"unknown dimensions: {unknown}")
    score = None if (missing or unknown) else sum(v.score for v in dims.values())  # type: ignore[union-attr]

    floor, floor_reasons = request_risk_floor(request_text)
    risk = max_risk(payload.risk, floor, risk_floor)
    if risk != payload.risk:
        reasons.append(f"risk raised from {payload.risk} to {risk} by policy floor")
    reasons.extend(floor_reasons)
    reasons.extend(payload.risk_reasons)

    if payload.forced_complex_reasons or (score is not None and score > cx.standard_max_score):
        path: Path_ = "complex"
        reasons.extend(payload.forced_complex_reasons)
    else:
        trivial_blockers = []
        if score is None:
            if not cx.unknown_dimensions_allow_trivial:
                trivial_blockers.append("unknowns never qualify for trivial")
        elif score > cx.trivial_max_score:
            trivial_blockers.append(f"score {score} > {cx.trivial_max_score}")
        if cx.trivial_requires_low_risk and risk != "low":
            trivial_blockers.append(f"risk {risk}")
        if cx.trivial_requires_single_atomic_task and not payload.single_atomic_task:
            trivial_blockers.append("not a single atomic task")
        if cx.trivial_requires_known_pattern and not payload.known_pattern:
            trivial_blockers.append("no known pattern")
        if payload.new_trust_boundary:
            trivial_blockers.append("new trust boundary")
        path = "standard" if trivial_blockers else "trivial"
        reasons.extend(f"not trivial: {b}" for b in trivial_blockers)
    return Classification(
        path=path,
        score=score,
        risk=risk,
        reasons=reasons,
        dimensions={k: (v.model_dump() if v else None) for k, v in dims.items()},
    )


def path_rank(path: str) -> int:
    return {"trivial": 0, "standard": 1, "complex": 2}[path]


def record_classification(store: Store, job_id: str, revision: int, cls: Classification, source: str) -> str:
    assessment_id = new_id("cx")
    with store.tx() as conn:
        conn.execute(
            "INSERT INTO complexity_assessments VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                assessment_id, job_id, revision, source, json.dumps(cls.dimensions), cls.score, cls.path, cls.risk,
                json.dumps(cls.reasons), cls.digest(), now(),
            ),
        )
        store.event(conn, job_id, "feature.classified", {"path": cls.path, "score": cls.score, "risk": cls.risk, "source": source})
    return assessment_id
