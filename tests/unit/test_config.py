import pytest
import yaml

from agent_factory.config import ConfigError, load_config

from ..conftest import make_factory_dir


def test_project_config_loads(tmp_path):
    cfg = load_config(make_factory_dir(tmp_path))
    assert cfg.factory.merge.human_interaction == "off"
    assert cfg.workflow.analysis.max_rounds_per_feature == 3
    assert len(cfg.policy_digest) == 64


def test_unquoted_off_is_a_boolean_and_rejected(tmp_path):
    d = make_factory_dir(tmp_path)
    text = (d / "config.yaml").read_text().replace("human_interaction: 'off'", "human_interaction: off")
    (d / "config.yaml").write_text(text)
    with pytest.raises(ConfigError, match="human_interaction"):
        load_config(d)


def test_duplicate_keys_rejected(tmp_path):
    d = make_factory_dir(tmp_path)
    (d / "selection.yaml").write_text((d / "selection.yaml").read_text() + "\nenabled: true\n")
    with pytest.raises(ConfigError, match="duplicate key"):
        load_config(d)


def test_unknown_fields_and_string_booleans_rejected(tmp_path):
    d = make_factory_dir(tmp_path)
    raw = yaml.safe_load((d / "workflow.yaml").read_text())
    raw["analysis"]["max_rounds_per_feature"] = 3
    raw["separate_spec_review_gate"] = False  # removed field must not come back
    (d / "workflow.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match="separate_spec_review_gate"):
        load_config(d)
    raw.pop("separate_spec_review_gate")
    raw["enabled"] = "true"
    (d / "workflow.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError):
        load_config(d)


@pytest.mark.parametrize("patch,match", [
    (("spec_evaluation", "human_approval", True), "no human spec approval"),
    (("spec_evaluation", "require_ai_pass", False), "cannot be disabled"),
    (("analysis", "reset_on_model_switch", True), "never reset"),
    (("analysis", "allow_scope_reduction_to_pass", True), "scope reduction"),
    (("paths", "trivial", "skip_required_code_checks", True), "never required code checks"),
])
def test_forbidden_workflow_settings(tmp_path, patch, match):
    d = make_factory_dir(tmp_path)
    raw = yaml.safe_load((d / "workflow.yaml").read_text())
    node = raw
    for key in patch[:-2]:
        node = node[key]
    node[patch[-2]] = patch[-1]
    (d / "workflow.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match=match):
        load_config(d)


def test_weights_must_sum_to_one(tmp_path):
    d = make_factory_dir(tmp_path)
    raw = yaml.safe_load((d / "selection.yaml").read_text())
    raw["weights"]["cost"] = 0.5
    (d / "selection.yaml").write_text(yaml.safe_dump(raw))
    with pytest.raises(ConfigError, match="sum to 1"):
        load_config(d)


def test_fake_runtime_not_configurable(tmp_path):
    d = make_factory_dir(tmp_path, **{"runtime.roles.builder.runtime": "fake"})
    with pytest.raises(ConfigError):
        load_config(d)
