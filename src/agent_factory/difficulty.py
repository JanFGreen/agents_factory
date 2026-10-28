"""Task difficulty: the planner's label, a structural floor from the task record, and an
optional second opinion from a typed decision model. The result only ever goes up.

The level picks the builder model (``routing.builder_models``); a hard leaf must be split
or say why it cannot be (decomposition review).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .policy import is_test_file
from .schemas import TaskSpec

LEVELS = ["easy", "medium", "hard"]

# Rubric shared by the decision model and the planner skill (factory-plan/references/task-schema.md).
CRITERIA = {
    "easy": "One responsibility, 1-2 production files, follows an existing pattern in the repo, no new interface, "
            "one obvious test level. A small model can do it from the task record alone.",
    "medium": "A few production files or a new internal interface, some state or error handling to design, "
              "tests at more than one level, or medium risk.",
    "hard": "Several modules or layers, a new public or cross-module contract, concurrency, migration or tricky "
            "state, unclear behaviour or several requirements at once. Should be split into smaller tasks.",
}
INSTRUCTIONS = ("How difficult is this software task for a coding agent that implements it with its tests in one "
                "attempt? Judge the task record only: its scope, contracts, risk and acceptance.")


@dataclass(frozen=True)
class Choice:
    choice: str
    confidence: float
    input_tokens: int = 0


class Decider(Protocol):
    name: str

    def choose(self, state: dict, instructions: str, criteria: dict[str, str]) -> Choice | None:
        """A typed choice among ``criteria`` keys, or None when unavailable (callers fall back)."""


def raise_to(level: str, minimum: str) -> str:
    return max(level, minimum, key=LEVELS.index)


def _is_test_path(path: str) -> bool:
    """A test file, or a test folder named on its own (``tests``, ``src/__tests__/``)."""
    return is_test_file(path) or is_test_file(path.rstrip("/") + "/x") or path.rstrip("/").rsplit("/", 1)[-1] in ("test", "__tests__")


def structural_floor(task: TaskSpec) -> tuple[str, list[str]]:
    """What the task record alone proves about the minimum difficulty."""
    level, reasons = "easy", []
    production = [p for p in task.file_scope if not _is_test_path(p)]
    folders = [p for p in production if "*" in p or "." not in p.rsplit("/", 1)[-1]]
    if folders:
        level = raise_to(level, "medium")
        reasons.append(f"scope names whole folders or globs: {folders}")
    if len(production) > 6:
        level = raise_to(level, "hard")
        reasons.append(f"{len(production)} production paths")
    elif len(production) > 3:
        level = raise_to(level, "medium")
        reasons.append(f"{len(production)} production paths")
    if task.risk != "low":
        level = raise_to(level, "medium")
        reasons.append(f"risk {task.risk}")
    if len(task.requirement_ids) > 3:
        level = raise_to(level, "medium")
        reasons.append(f"{len(task.requirement_ids)} requirements")
    if len(task.outputs) > 2:
        level = raise_to(level, "medium")
        reasons.append(f"{len(task.outputs)} output contracts")
    return level, reasons


def assess(task: TaskSpec, decider: Decider | None, min_confidence: float) -> dict:
    """Highest of the planner's label (medium when absent), the structural floor and a confident decider."""
    floor, reasons = structural_floor(task)
    planner = task.difficulty or "medium"
    level = raise_to(planner, floor)
    opinion = None
    if decider is not None:
        state = task.model_dump(include={"task_id", "requirement_ids", "depends_on", "inputs", "outputs", "file_scope",
                                         "risk", "acceptance", "verification", "difficulty_reasons"})
        choice = decider.choose(state, INSTRUCTIONS, CRITERIA)
        if choice is not None and choice.choice in LEVELS:
            used = choice.confidence >= min_confidence
            opinion = {"by": decider.name, "choice": choice.choice, "confidence": round(choice.confidence, 3),
                       "used": used, "input_tokens": choice.input_tokens}
            if used and LEVELS.index(choice.choice) > LEVELS.index(level):
                level = choice.choice
                reasons.append(f"{decider.name}: {choice.choice} (confidence {choice.confidence:.2f})")
    return {"difficulty": level, "planner": task.difficulty, "floor": floor, "decider": opinion,
            "reasons": [*task.difficulty_reasons, *reasons]}


def indivisible(task: TaskSpec) -> bool:
    """The planner stated why a hard task cannot be split (``difficulty_reasons: ["indivisible: ..."]``)."""
    return any(r.strip().lower().startswith("indivisible:") for r in task.difficulty_reasons)


def builder_level(difficulty: str, attempt: int, escalate: bool) -> str:
    """Attempt 1 uses the task's level; each later attempt one level up when escalation is on."""
    steps = max(0, attempt - 1) if escalate else 0
    return LEVELS[min(LEVELS.index(difficulty) + steps, len(LEVELS) - 1)]
