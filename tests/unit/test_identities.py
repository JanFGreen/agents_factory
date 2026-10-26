from agent_factory.identities import (
    ChangeKind, InputIdentity, ResultKind, digest, invalidated_kinds, stale_fields, tasks_plan_digest, tasks_progress_digest,
)

SHA = "a" * 40
D = "b" * 64


def ident(**kw):
    base = dict(head_sha=SHA, spec_digest=D, plan_digest=D, policy_digest=D, checks_digest=D, scope_digest=D, feature_revision=1)
    base.update(kw)
    return InputIdentity(**base)


def test_digest_is_order_independent():
    assert digest({"a": 1, "b": [1, 2]}) == digest({"b": [1, 2], "a": 1})


def test_code_review_stale_on_new_head_but_not_on_unrelated_field():
    old = ident()
    assert stale_fields(ResultKind.CODE_REVIEW, old, ident(head_sha="c" * 40)) == ["head_sha"]
    assert stale_fields(ResultKind.CODE_REVIEW, old, ident(decision_digest="d" * 64)) == []


def test_spec_change_invalidates_spec_eval_and_code_results():
    kinds = invalidated_kinds(ChangeKind.SCOPE)
    assert {ResultKind.SPEC_EVALUATION, ResultKind.DECOMPOSITION_REVIEW, ResultKind.CODE_REVIEW, ResultKind.FEATURE_ACCEPTANCE} <= kinds


def test_base_change_invalidates_integration_and_human_approval():
    assert {ResultKind.INTEGRATION, ResultKind.HUMAN_APPROVAL, ResultKind.GATE} <= invalidated_kinds(ChangeKind.BASE)


def test_policy_change_invalidates_everything_dependent():
    assert ResultKind.SPEC_EVALUATION in invalidated_kinds(ChangeKind.POLICY)
    assert ResultKind.GATE in invalidated_kinds(ChangeKind.POLICY)


def test_formatting_only_defaults_to_invalidation():
    assert ResultKind.SPEC_EVALUATION in invalidated_kinds(ChangeKind.FORMATTING_ONLY)


def test_placeholders_rejected():
    problems = InputIdentity(head_sha="<actual-git-sha>", spec_digest="<actual-sha256>").validate_real()
    assert len(problems) == 2


def test_tasks_md_checkbox_progress_is_not_a_plan_change():
    before = "## Tasks\n- [ ] TASK-01 do a\n- [ ] TASK-02 do b\n"
    after = "## Tasks\n- [x] TASK-01 do a\n- [ ] TASK-02 do b\n"
    assert tasks_plan_digest(before) == tasks_plan_digest(after)
    assert tasks_progress_digest(before) != tasks_progress_digest(after)
    assert tasks_plan_digest(before) != tasks_plan_digest(before.replace("do b", "do c"))
