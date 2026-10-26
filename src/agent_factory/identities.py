"""Input identities, digests and the invalidation matrix (blueprint §5).

A verdict is only evidence for the exact inputs it was computed on. Each result kind
declares which identity fields it depends on; a stored result is *current* only when
every one of those fields equals the job's current identity.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def digest(value: Any) -> str:
    if isinstance(value, bytes):
        return hashlib.sha256(value).hexdigest()
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def is_git_sha(value: str | None) -> bool:
    return bool(value) and bool(_HEX40.match(value))  # type: ignore[arg-type]


def is_sha256(value: str | None) -> bool:
    return bool(value) and bool(_HEX64.match(value))  # type: ignore[arg-type]


def directory_manifest(root: Path, ignore: tuple[str, ...] = ()) -> dict[str, str]:
    """Sorted path → sha256 manifest of every file under ``root``."""
    manifest: dict[str, str] = {}
    if not root.exists():
        return manifest
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if any(rel.startswith(prefix) for prefix in ignore):
            continue
        if rel.endswith("tasks.md"):
            manifest[rel] = tasks_plan_digest(path.read_text(encoding="utf-8"))
        else:
            manifest[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return manifest


_CHECKBOX = re.compile(r"^(\s*[-*]\s+)\[[ xX]\]", re.MULTILINE)


def tasks_plan_digest(text: str) -> str:
    """Digest of tasks.md content with checkbox progress normalised away.

    Ticking a box is progress, not a change of requirements, so it must not
    invalidate spec/plan evaluations.
    """
    return digest(_CHECKBOX.sub(r"\1[ ]", text))


def tasks_progress_digest(text: str) -> str:
    return digest(re.findall(r"^\s*[-*]\s+\[([ xX])\]", text, re.MULTILINE))


class InputIdentity(BaseModel):
    """Full identity of a verdict's inputs. Non-applicable fields are explicit ``None``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    repo_id: str | None = None
    job_id: str | None = None
    task_id: str | None = None
    attempt_id: str | None = None
    base_sha: str | None = None
    head_sha: str | None = None
    candidate_tree_sha: str | None = None
    feature_revision: int | None = None
    complexity_digest: str | None = None
    scope_digest: str | None = None
    spec_digest: str | None = None
    decision_digest: str | None = None
    plan_digest: str | None = None
    decomposition_digest: str | None = None
    analysis_round_id: str | None = None
    policy_digest: str | None = None
    checks_digest: str | None = None
    environment_digest: str | None = None
    runtime_version: str | None = None
    model_id: str | None = None
    prompt_digest: str | None = None
    skill_digests: dict[str, str] | None = None
    acceptance_suite_digest: str | None = None
    grader_version: str | None = None

    def validate_real(self) -> list[str]:
        """Reject placeholders such as ``<actual-git-sha>`` in a real result."""
        problems = []
        for name in ("base_sha", "head_sha", "candidate_tree_sha"):
            value = getattr(self, name)
            if value is not None and not is_git_sha(value):
                problems.append(f"{name} is not a git sha: {value!r}")
        for name in (
            "complexity_digest", "scope_digest", "spec_digest", "decision_digest", "plan_digest",
            "decomposition_digest", "policy_digest", "checks_digest", "environment_digest",
            "prompt_digest", "acceptance_suite_digest",
        ):
            value = getattr(self, name)
            if value is not None and not is_sha256(value):
                problems.append(f"{name} is not a sha256 digest: {value!r}")
        return problems


class ResultKind(StrEnum):
    CLASSIFICATION = "classification"
    OPTION_SELECTION = "option-selection"
    SPEC_EVALUATION = "spec-evaluation"
    DECOMPOSITION_REVIEW = "decomposition-review"
    GATE = "gate"
    CODE_REVIEW = "code-review"
    SECURITY_REVIEW = "security-review"
    UI_REVIEW = "ui-review"
    PRODUCT_VERIFY = "product-verify"
    TEST_ADEQUACY = "test-adequacy"
    FEATURE_ACCEPTANCE = "feature-acceptance"
    INTEGRATION = "integration"
    HUMAN_APPROVAL = "human-approval"


_ANALYSIS = ("feature_revision", "scope_digest", "policy_digest")
_CODE = ("head_sha", "spec_digest", "plan_digest", "policy_digest", "checks_digest")

# Invalidation matrix as code: which identity fields each result depends on.
RESULT_DEPENDENCIES: dict[ResultKind, tuple[str, ...]] = {
    ResultKind.CLASSIFICATION: (*_ANALYSIS,),
    ResultKind.OPTION_SELECTION: (*_ANALYSIS, "complexity_digest"),
    ResultKind.SPEC_EVALUATION: (*_ANALYSIS, "spec_digest", "decision_digest"),
    ResultKind.DECOMPOSITION_REVIEW: (*_ANALYSIS, "spec_digest", "decision_digest", "plan_digest"),
    ResultKind.GATE: (*_CODE, "environment_digest", "base_sha"),
    ResultKind.CODE_REVIEW: _CODE,
    ResultKind.SECURITY_REVIEW: _CODE,
    ResultKind.UI_REVIEW: _CODE,
    ResultKind.PRODUCT_VERIFY: (*_CODE, "acceptance_suite_digest"),
    ResultKind.TEST_ADEQUACY: _CODE,
    ResultKind.FEATURE_ACCEPTANCE: (*_CODE, "scope_digest", "acceptance_suite_digest"),
    ResultKind.INTEGRATION: (*_CODE, "base_sha", "candidate_tree_sha", "acceptance_suite_digest"),
    ResultKind.HUMAN_APPROVAL: ("head_sha", "base_sha", "spec_digest", "policy_digest"),
}


def stale_fields(kind: ResultKind, recorded: InputIdentity, current: InputIdentity) -> list[str]:
    """Return the dependency fields that differ; empty list means the result is current."""
    stale = []
    for field in RESULT_DEPENDENCIES[kind]:
        if getattr(recorded, field) != getattr(current, field):
            stale.append(field)
    return stale


class ChangeKind(StrEnum):
    SCOPE = "scope"
    PLAN = "plan"
    CODE = "code"
    BASE = "base"
    POLICY = "policy"
    FORMATTING_ONLY = "formatting-only"


def invalidated_kinds(change: ChangeKind) -> set[ResultKind]:
    """Human-readable view of the matrix, derived from RESULT_DEPENDENCIES."""
    fields = {
        ChangeKind.SCOPE: {"scope_digest", "feature_revision", "spec_digest"},
        ChangeKind.PLAN: {"plan_digest"},
        ChangeKind.CODE: {"head_sha"},
        ChangeKind.BASE: {"base_sha"},
        ChangeKind.POLICY: {"policy_digest", "checks_digest"},
        # Default is invalidation unless an explicit equivalence event is recorded.
        ChangeKind.FORMATTING_ONLY: {"spec_digest"},
    }[change]
    return {kind for kind, deps in RESULT_DEPENDENCIES.items() if fields & set(deps)}
