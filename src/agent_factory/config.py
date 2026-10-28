"""Strict loading of factory configuration (config.yaml, workflow.yaml, selection.yaml).

Rules from the blueprint: duplicate keys and unknown fields are errors, booleans are
real booleans, and ``merge.human_interaction`` is text (YAML ``off`` parses as a
boolean and is therefore rejected instead of guessed).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

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
    # After the plan is accepted the job stops with a cost estimate until the operator approves it
    # (`factory plan approve`); estimates up to `auto_approve_below_usd` go ahead on their own.
    plan_approval: Literal["required", "off"] = "required"
    auto_approve_below_usd: float = Field(default=5.0, ge=0)


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


AgentRuntimeName = Literal["claude", "codex", "opencode"]
IMPLEMENTED_RUNTIMES = {"claude", "codex", "opencode"}  # each has an adapter in adapters/


class AgentRuntimeDef(Strict):
    """How an agent program is launched (models.yaml `runtimes`)."""

    binary: str  # a command on PATH, or a path relative to the factory checkout (tools/...)
    min_cli_version: str | None = None
    # Variables the agent process receives, as {name inside the agent: name in the controller environment}.
    # Only these pass; the agent can read them, so prefer a key-holding proxy for shared keys.
    pass_env: dict[str, str] = {}


class EndpointDef(Strict):
    """An OpenAI-compatible model server (Ollama, LM Studio, llama.cpp, vLLM), local or on another machine."""

    base_url: str = Field(pattern=r"^https?://")
    models: list[str] = Field(min_length=1)
    max_concurrent: int = Field(default=1, ge=1, le=64)
    key_env: str | None = None  # variable with the server's API key, when it needs one


class FallbackBinding(Strict):
    """Used when the role's runtime is not installed or none of its endpoints is free and reachable."""

    runtime: AgentRuntimeName
    model: str
    effort: Literal["low", "medium", "high", "max"] | None = None


class RoleBinding(Strict):
    runtime: AgentRuntimeName  # the fake runtime is injected by tests only, never configurable
    model: str
    effort: Literal["low", "medium", "high", "max"] | None = None
    tools: list[str]
    max_turns: int | None = None
    endpoints: list[str] = []  # opencode only: a pool of servers that serve `model`; a free one is picked per call
    fallback: FallbackBinding | None = None


class ModelPrice(Strict):
    input_per_mtok: float = Field(ge=0)
    output_per_mtok: float = Field(ge=0)


class Runtime(Strict):
    claude_binary: str
    min_cli_version: str = "2.1.280"  # the 5.x models are rejected by older CLIs
    sandbox: Literal["required"]
    roles: dict[str, RoleBinding]
    runtimes: dict[AgentRuntimeName, AgentRuntimeDef] = {}
    endpoints: dict[str, EndpointDef] = {}
    prices: dict[str, ModelPrice] = {}  # for runtimes that report tokens but not cost (codex)

    @model_validator(mode="after")
    def _roles_use_available_runtimes(self) -> "Runtime":
        for role, binding in self.roles.items():
            for runtime in (binding.runtime, *([binding.fallback.runtime] if binding.fallback else [])):
                if runtime not in IMPLEMENTED_RUNTIMES:
                    raise ValueError(f"role {role!r} uses runtime {runtime!r}, which has no adapter yet "
                                     f"(implemented: {sorted(IMPLEMENTED_RUNTIMES)}); bind the role to claude in models.yaml")
                if self.runtimes and runtime not in self.runtimes:
                    raise ValueError(f"role {role!r} uses runtime {runtime!r}, which models.yaml `runtimes` does not define")
            if binding.endpoints and binding.runtime != "opencode":
                raise ValueError(f"role {role!r}: endpoints need runtime opencode (it speaks to OpenAI-compatible servers)")
            for name in binding.endpoints:
                endpoint = self.endpoints.get(name)
                if endpoint is None:
                    raise ValueError(f"role {role!r} uses endpoint {name!r}, which models.yaml `endpoints` does not define")
                if binding.model not in endpoint.models:
                    raise ValueError(f"role {role!r}: endpoint {name!r} does not serve {binding.model!r} (serves {endpoint.models})")
        return self


_REVIEWS = Literal["code-review", "security-review", "product-verify", "test-adequacy", "ui-review"]


class ReviewPolicy(Strict):
    """Task review cost controls. Feature acceptance and integration always use the full role bindings."""

    # Tiers run in order; a CHANGES in a tier skips the later tiers for this attempt (they run once it passes).
    tiers: list[list[_REVIEWS]] = [["code-review", "test-adequacy", "ui-review"], ["product-verify", "security-review"]]
    # Task reviews of jobs at these risk levels use `cheaper_models` for the listed roles.
    cheaper_models_for_risk: list[Literal["low", "medium", "high"]] = ["low"]
    cheaper_models: dict[str, str] = {}

    @model_validator(mode="after")
    def _every_review_once(self) -> "ReviewPolicy":
        flat = [r for tier in self.tiers for r in tier]
        if sorted(flat) != sorted(set(flat)) or set(flat) != set(_REVIEWS.__args__):
            raise ValueError("review.tiers must list every task review exactly once")
        if "high" in self.cheaper_models_for_risk:
            raise ValueError("high-risk tasks always get the full reviewers")
        return self


class SpecPolicy(Strict):
    """Who settles the product decisions a request leaves open (chosen per job at submit)."""

    # guided: the job stops after intake until the operator answers them; auto: the proposed answers are assumed.
    default_mode: Literal["guided", "auto"] = "guided"
    max_decisions: int = Field(default=7, ge=1, le=15)


class DesignPolicy(Strict):
    """The target repository's design direction: the visual contract UI work is built and reviewed against."""

    doc: str = "docs/DESIGN.md"  # repo-relative; written by the first UI task when missing
    references_dir: str = "docs/design"  # reference images (png/jpg/webp) the UI is compared with
    min_visual_score: int = Field(default=3, ge=1, le=5)  # a served UI review PASS with a lower score becomes CHANGES

    @field_validator("doc", "references_dir")
    @classmethod
    def _relative(cls, path: str) -> str:
        if path.startswith("/") or ".." in path.split("/"):
            raise ValueError(f"design paths are repo-relative: {path!r}")
        return path


Difficulty = Literal["easy", "medium", "hard"]


class Routing(Strict):
    """Builder model per task difficulty; an empty map keeps the builder role binding."""

    builder_models: dict[Difficulty, str] = {}
    # Each further attempt of a task uses the model one difficulty level up (hard stays hard).
    escalate_on_retry: bool = True
    # First attempt of a trivial-path feature (atomic, low risk, known pattern); retries use `builder_models`.
    trivial_path_model: str | None = None


class DecisionModel(Strict):
    """Optional typed decision model (second opinion on task difficulty). Skipped when its key is absent."""

    provider: Literal["none", "jev"] = "none"
    model: str = "jev-latest"
    url: str = "https://api.typesafe.ai/v1/systemone"
    api_key_env: str = "TYPESAFE_API_KEY"  # read by the controller only; never passed to agents
    min_confidence: float = Field(default=0.6, ge=0, le=1)
    timeout_seconds: float = Field(default=10, gt=0, le=60)


class BrowserConfig(Strict):
    """Browser for UI reviewers (Playwright CLI), used when the checks manifest declares a preview."""

    tool: Literal["playwright-cli"] = "playwright-cli"
    binary: str  # relative to the factory checkout (pinned under tools/)
    viewports: list[str] = Field(default=["390x844", "1280x800"], min_length=1)
    idle_timeout_ms: int = Field(default=300_000, ge=10_000)  # a session left open by an agent ends on its own

    @field_validator("viewports")
    @classmethod
    def _sizes(cls, sizes: list[str]) -> list[str]:
        for size in sizes:
            w, sep, h = size.partition("x")
            if not sep or not (w.isdigit() and h.isdigit()):
                raise ValueError(f"viewport must look like 390x844: {size!r}")
        return sizes


class FactoryConfig(Strict):
    schema_version: Literal[1]
    data_dir: str
    execution: Execution
    attempts: Attempts
    budget: Budget
    policy: Policy
    merge: Merge
    runtime: Runtime
    review: ReviewPolicy = ReviewPolicy()
    spec: SpecPolicy = SpecPolicy()
    browser: BrowserConfig | None = None
    design: DesignPolicy = DesignPolicy()
    routing: Routing = Routing()
    decision_model: DecisionModel = DecisionModel()


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


class ModelsFile(Strict):
    """factory/models.yaml: every model choice in one place (who runs which role, routing, reviews)."""

    schema_version: Literal[1]
    runtimes: dict[str, dict[str, Any]]
    roles: dict[str, dict[str, Any]]
    endpoints: dict[str, dict[str, Any]] = {}
    prices: dict[str, dict[str, Any]] = {}
    routing: dict[str, Any] = {}
    review_models: dict[str, Any] = {}
    decision_model: dict[str, Any] = {}


def _merge_models(raw_factory: dict, raw_models: Any, source: Path) -> dict:
    """Place models.yaml into the config tree; a model setting left in config.yaml is an error."""
    runtime, review = raw_factory.get("runtime") or {}, raw_factory.get("review") or {}
    moved = [k for k in ("routing", "decision_model") if k in raw_factory]
    moved += [f"runtime.{k}" for k in ("roles", "claude_binary", "min_cli_version", "runtimes", "endpoints", "prices") if k in runtime]
    moved += [f"review.{k}" for k in ("cheaper_models", "cheaper_models_for_risk") if k in review]
    if moved:
        raise ConfigError(f"{source.parent / 'config.yaml'}: {', '.join(moved)} belong in models.yaml only")
    models = _validate(ModelsFile, raw_models, source)
    if "claude" not in models.runtimes:
        raise ConfigError(f"{source}: runtimes.claude is required (the default agent runtime)")
    claude = models.runtimes["claude"]
    merged = {**raw_factory, "runtime": {**runtime, "claude_binary": claude.get("binary"), "roles": models.roles,
                                          "runtimes": models.runtimes, "endpoints": models.endpoints, "prices": models.prices,
                                          **({"min_cli_version": claude["min_cli_version"]} if "min_cli_version" in claude else {})}}
    if models.routing:
        merged["routing"] = models.routing
    if models.decision_model:
        merged["decision_model"] = models.decision_model
    if models.review_models:
        extra = set(models.review_models) - {"for_risk", "models"}
        if extra:
            raise ConfigError(f"{source}: review_models accepts for_risk and models, not {sorted(extra)}")
        merged["review"] = {**review, "cheaper_models_for_risk": models.review_models.get("for_risk", []),
                            "cheaper_models": models.review_models.get("models", {})}
    return merged


def load_config(factory_dir: Path) -> LoadedConfig:
    factory_dir = factory_dir.resolve()
    raw_factory = load_yaml(factory_dir / "config.yaml")
    raw_models = load_yaml(factory_dir / "models.yaml") if (factory_dir / "models.yaml").exists() else None
    if raw_models is not None:
        raw_factory = _merge_models(raw_factory, raw_models, factory_dir / "models.yaml")
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
        policy_digest=digest({"factory": raw_factory, "workflow": raw_workflow, "selection": raw_selection, "models": raw_models}),
    )
