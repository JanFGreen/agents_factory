"""Strict loading of factory configuration (config.yaml, workflow.yaml, selection.yaml).

Rules from the blueprint: duplicate keys and unknown fields are errors, booleans are
real booleans, and ``merge.human_interaction`` is text (YAML ``off`` parses as a
boolean and is therefore rejected instead of guessed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .identities import digest


class ConfigError(ValueError):
    pass


class _UniqueKeyLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode, deep: bool = False) -> dict:
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in mapping:
            raise ConfigError(f"duplicate key {key!r} at line {key_node.start_mark.line + 1}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def load_yaml(path: Path) -> Any:
    try:
        return yaml.load(path.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path}: {exc}") from exc


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


# --- workflow.yaml -------------------------------------------------------------------


class FeatureInput(Strict):
    accepted_sources: list[Literal["file", "cli", "github_issue"]]
    preserve_original_request: bool
    immutable_source_snapshot: bool
    required_fields: list[str]


ComplexityDimension = Literal["scope", "coupling", "state_and_data", "interfaces", "uncertainty", "verification"]


class Complexity(Strict):
    dimensions: list[ComplexityDimension]
    dimension_min: int
    dimension_max: int
    trivial_max_score: int
    standard_max_score: int
    trivial_requires_low_risk: bool
    trivial_requires_single_atomic_task: bool
    trivial_requires_known_pattern: bool
    unknown_dimensions_allow_trivial: bool
    reclassify_on_actual_diff: bool
    risk_is_separate: bool


class TrivialPath(Strict):
    spec_format: Literal["micro"]
    minimum_options: int
    planned_tasks: int
    skip_decomposition_loop: bool
    skip_required_code_checks: bool

    @model_validator(mode="after")
    def _checks_never_skipped(self) -> "TrivialPath":
        if self.skip_required_code_checks:
            raise ValueError("fast path may skip decomposition, never required code checks")
        return self


class FullPath(Strict):
    spec_format: Literal["openspec"]
    minimum_options: int = Field(ge=2)
    require_decomposition: bool


class Paths(Strict):
    trivial: TrivialPath
    standard: FullPath
    complex: FullPath


class Analysis(Strict):
    max_rounds_per_feature: int = Field(ge=1)
    count_initial_round: bool
    shared_across_spec_options_and_decomposition: bool
    reset_on_child_task: bool
    reset_on_model_switch: bool
    reset_on_replan: bool
    stop_when_all_required_checks_pass: bool
    max_consecutive_rounds_without_progress: int
    max_feasibility_spikes_per_feature: int
    on_limit: Literal["ANALYSIS_LIMIT_REACHED"]
    allow_scope_reduction_to_pass: bool

    @model_validator(mode="after")
    def _no_resets(self) -> "Analysis":
        if self.reset_on_child_task or self.reset_on_model_switch or self.reset_on_replan:
            raise ValueError("analysis round counter must never reset")
        if self.allow_scope_reduction_to_pass:
            raise ValueError("scope reduction to pass is forbidden")
        return self


SpecDimension = Literal["intent_coverage", "consistency", "testability", "feasibility", "risk_controls"]


class SpecEvaluation(Strict):
    enabled: bool
    timing: Literal["before_build"]
    require_ai_pass: bool
    human_approval: bool
    independent_reviewer_context: bool
    mandatory_dimensions: list[SpecDimension]

    @model_validator(mode="after")
    def _ai_gate(self) -> "SpecEvaluation":
        if not (self.enabled and self.require_ai_pass):
            raise ValueError("AI spec evaluation cannot be disabled in this variant")
        if self.human_approval:
            raise ValueError("this variant has no human spec approval")
        return self


class Decomposition(Strict):
    max_depth: int
    max_leaf_tasks_per_feature: int
    target_task_minutes: int
    full_requirement_coverage: bool
    require_acyclic_dependencies: bool
    require_parent_end_to_end_acceptance: bool
    future_scope_policy: Literal["record_constraints_do_not_build_speculative_features"]


class Implementation(Strict):
    max_attempts_per_leaf_task: int
    max_build_attempts_per_feature: int
    integration_rebuilds: int
    attempts_do_not_reset_after_reclassification: bool


class WorkflowConfig(Strict):
    enabled: bool
    schema_version: Literal[2]
    feature_input: FeatureInput
    complexity: Complexity
    paths: Paths
    analysis: Analysis
    spec_evaluation: SpecEvaluation
    decomposition: Decomposition
    implementation: Implementation


# --- selection.yaml ------------------------------------------------------------------


class Weights(Strict):
    goal_fit: float
    correctness: float
    maintainability: float
    delivery_time: float
    cost: float

    @model_validator(mode="after")
    def _sum_to_one(self) -> "Weights":
        total = self.goal_fit + self.correctness + self.maintainability + self.delivery_time + self.cost
        if abs(total - 1.0) > 1e-9:
            raise ValueError(f"weights must sum to 1, got {total}")
        return self


class Ratings(Strict):
    min: int
    max: int
    higher_is_better: Literal[True]


class SelectionConfig(Strict):
    enabled: bool
    schema_version: Literal[2]
    objective: Literal["best_feasible_solution"]
    weights: Weights
    ratings: Ratings
    hard_constraints: list[str]
    near_tie_delta: float
    tie_break: list[
        Literal["correctness_desc", "maintainability_desc", "estimated_cost_asc", "estimated_time_asc", "option_id_asc"]
    ]
    require_exactly_one_selected_option: Literal[True]
    human_spec_approval: Literal[False]
    ai_spec_evaluation: Literal[True]
    require_ai_spec_eval_pass: Literal[True]
    analysis_policy_file: str


# --- config.yaml ---------------------------------------------------------------------


class Execution(Strict):
    max_active_jobs: int = Field(ge=1)
    max_agent_processes: int = Field(ge=1)
    max_builders_per_job: int = Field(ge=1, le=8)  # >1: independent ready tasks build in parallel lanes
    max_test_processes: int = Field(ge=1)
    max_integration_candidates: int = Field(ge=1)
    lease_ttl_seconds: int
    heartbeat_seconds: int
    command_timeout_seconds: int
    reviewer_timeout_seconds: int
    agent_stall_seconds: int = Field(default=300, ge=30)  # no new runtime event for this long => stalled run
    job_deadline_minutes: int

    @model_validator(mode="after")
    def _lanes_fit_agent_slots(self) -> "Execution":
        if self.max_builders_per_job > self.max_agent_processes:
            raise ValueError("max_builders_per_job cannot exceed max_agent_processes (each lane runs one agent at a time)")
        return self


class Attempts(Strict):
    intake: int
    feature_analysis_rounds: int
    task_build_and_review: int
    total_feature_build_attempts: int
    integration_rebuilds: int
    structured_output_repair: int = Field(ge=0, le=1)


class Budget(Strict):
    currency: Literal["USD"]
    hard_limit_per_job: float = Field(gt=0)
    warning_fraction: float
    reserve_for_verification_fraction: float = Field(ge=0, lt=1)
    unknown_price_policy: Literal["require_operator_limit", "block_paid_calls"]
    max_cost_per_agent_call: float = Field(gt=0)


class Policy(Strict):
    mode: Literal["offline", "local", "pr", "automerge", "release"]
    deploy: bool
    external_messages: bool
    protected_paths: list[str]
    risk_path_rules: dict[str, list[str]]


class Merge(Strict):
    enabled: bool
    human_interaction: Literal["off", "before_merge", "on_risk"]
    human_review_risk_levels: list[Literal["low", "medium", "high"]]
    approval_timeout_action: Literal["wait", "cancel"]
    require_current_head: Literal[True]
    require_required_checks: Literal[True]
    allow_admin_bypass: Literal[False]


class RoleBinding(Strict):
    runtime: Literal["claude"]  # the fake runtime is injected by tests only, never configurable
    model: str
    effort: Literal["low", "medium", "high", "max"] | None = None
    tools: list[str]
    max_turns: int | None = None


class Runtime(Strict):
    claude_binary: str
    sandbox: Literal["required"]
    roles: dict[str, RoleBinding]


class FactoryConfig(Strict):
    schema_version: Literal[1]
    data_dir: str
    execution: Execution
    attempts: Attempts
    budget: Budget
    policy: Policy
    merge: Merge
    runtime: Runtime


class LoadedConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    factory: FactoryConfig
    workflow: WorkflowConfig
    selection: SelectionConfig
    factory_dir: Path
    policy_digest: str

    def data_dir(self) -> Path:
        path = Path(self.factory.data_dir).expanduser()
        if not path.is_absolute():
            path = (self.factory_dir.parent / path).resolve()
        return path


def _validate(model: type[Strict], raw: Any, source: Path) -> Any:
    if not isinstance(raw, dict):
        raise ConfigError(f"{source}: expected a mapping")
    try:
        return model.model_validate(raw)
    except ValueError as exc:
        raise ConfigError(f"{source}: {exc}") from exc


def load_config(factory_dir: Path) -> LoadedConfig:
    factory_dir = factory_dir.resolve()
    raw_factory = load_yaml(factory_dir / "config.yaml")
    raw_workflow = load_yaml(factory_dir / "workflow.yaml")
    raw_selection = load_yaml(factory_dir / "selection.yaml")
    factory = _validate(FactoryConfig, raw_factory, factory_dir / "config.yaml")
    workflow = _validate(WorkflowConfig, raw_workflow, factory_dir / "workflow.yaml")
    selection = _validate(SelectionConfig, raw_selection, factory_dir / "selection.yaml")
    if workflow.analysis.max_rounds_per_feature != factory.attempts.feature_analysis_rounds:
        raise ConfigError("workflow.analysis.max_rounds_per_feature must equal attempts.feature_analysis_rounds")
    return LoadedConfig(
        factory=factory,
        workflow=workflow,
        selection=selection,
        factory_dir=factory_dir,
        policy_digest=digest({"factory": raw_factory, "workflow": raw_workflow, "selection": raw_selection}),
    )
