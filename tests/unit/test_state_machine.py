"""State machine properties: illegal transitions are refused and guarded states need current evidence."""

from hypothesis import given, settings, strategies as st

from agent_factory.controller import TERMINAL, S, legal


def test_terminal_states_have_no_exits():
    for src in TERMINAL:
        assert not any(legal(src, dst) for dst in S)


def test_no_shortcut_from_analysis_to_merge():
    for src in (S.INTAKE, S.MICRO_SPEC, S.AI_SPEC_EVALUATION, S.BUILD_TASK, S.CHECKS, S.REVIEW):
        assert not legal(src, S.MERGED)
        assert not legal(src, S.MERGING)


def test_no_human_spec_approval_state_exists():
    assert "AWAITING_SPEC_APPROVAL" not in S.__members__


@settings(max_examples=300, deadline=None)
@given(st.lists(st.sampled_from(list(S)), min_size=1, max_size=40))
def test_random_walks_never_reach_merged_without_merging(walk):
    state = S.FEATURE_RECEIVED
    for target in walk:
        if legal(state, target):
            if target is S.MERGED:
                assert state is S.MERGING
            state = target


def test_guards_refuse_without_evidence(make_env):
    import pytest

    from agent_factory.controller import TransitionRefused

    env = make_env("trivial")
    job = env.submit()
    with env.store.tx() as conn:
        env.store.cas_update_job(conn, job, env.store.job(job)["version"], state="AI_SPEC_EVALUATION", path="trivial")
    with pytest.raises(TransitionRefused, match="no spec-evaluation result"):
        env.factory.controller.transition(job, S.SINGLE_TASK_READY)
    with env.store.tx() as conn:
        env.store.cas_update_job(conn, job, env.store.job(job)["version"], state="READY_TO_MERGE")
    with pytest.raises(TransitionRefused):
        env.factory.controller.transition(job, S.MERGING)
