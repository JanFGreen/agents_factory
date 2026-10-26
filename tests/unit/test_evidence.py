import json

import pytest

from agent_factory.evidence import EvidenceError, EvidenceStore, Producer
from agent_factory.identities import InputIdentity, ResultKind
from agent_factory.schemas import ContractError, parse_envelope
from agent_factory.state import Store

SHA = "a" * 40


@pytest.fixture
def ev(tmp_path):
    store = Store(tmp_path / "state.db")
    return EvidenceStore(store, tmp_path)


def ident(head=SHA):
    return InputIdentity(job_id="job-1", head_sha=head, spec_digest="b" * 64, plan_digest="b" * 64, policy_digest="c" * 64, checks_digest="d" * 64)


def test_signed_result_is_current_until_inputs_change(ev):
    ev.record_result(job_id="job-1", kind=ResultKind.CODE_REVIEW, verdict="PASS", identity=ident(), producer=Producer("agent-adapter", "code_reviewer", "run-1"), payload={})
    assert ev.current_pass("job-1", ResultKind.CODE_REVIEW, ident())[0]
    ok, why = ev.current_pass("job-1", ResultKind.CODE_REVIEW, ident("e" * 40))
    assert not ok and "head_sha" in why


def test_af08_forged_pass_row_is_rejected(ev):
    ev.record_result(job_id="job-1", kind=ResultKind.GATE, verdict="FAIL_CODE", identity=ident(), producer=Producer("trusted-runner", "gate"), payload={})
    ev.store.conn.execute("UPDATE results SET verdict = 'PASS'")  # tampering with the DB row
    ok, why = ev.current_pass("job-1", ResultKind.GATE, ident())
    assert not ok and "signature is invalid" in why


def test_af08_untrusted_producer_cannot_record(ev):
    with pytest.raises(EvidenceError, match="untrusted producer"):
        ev.record_result(job_id="job-1", kind=ResultKind.GATE, verdict="PASS", identity=ident(), producer=Producer("builder", "builder"), payload={})


def test_af07_placeholder_identity_rejected(ev):
    with pytest.raises(EvidenceError, match="not a git sha"):
        ev.record_result(job_id="job-1", kind=ResultKind.CODE_REVIEW, verdict="PASS", identity=InputIdentity(head_sha="<actual-git-sha>"),
                         producer=Producer("agent-adapter", "r"), payload={})


def test_af07_empty_or_fake_review_file_gives_no_rights(ev):
    assert ev.current_pass("job-1", ResultKind.CODE_REVIEW, ident()) == (False, "no code-review result")


def test_artifact_tamper_detected(ev):
    aid = ev.put(b"log", "log", Producer("trusted-runner", "x"), "job-1")
    row = ev.store.one("SELECT rel_path FROM artifacts WHERE artifact_id = ?", (aid,))
    (ev.root / row["rel_path"]).write_bytes(b"forged")
    with pytest.raises(EvidenceError, match="does not match"):
        ev.read(aid)


def test_signing_key_is_private(ev, tmp_path):
    assert oct((tmp_path / "keys" / "evidence.key").stat().st_mode & 0o777) == "0o600"


def envelope(**kw):
    base = {"schema_version": "1", "kind": "code-review", "verdict": "PASS", "summary": "s", "findings": [], "payload": {}}
    base.update(kw)
    return base


def test_pass_with_open_blocking_finding_is_contract_violation():
    finding = {"id": "F-1", "severity": "high", "blocking": True, "problem": "leak", "status": "open"}
    with pytest.raises(ContractError, match="open blocking"):
        parse_envelope(envelope(findings=[finding]), "code-review")
    parse_envelope(envelope(findings=[dict(finding, status="fixed")]), "code-review")


def test_envelope_parsing_rules():
    text = "Thinking...\n```json\n" + json.dumps(envelope()) + "\n```"
    env, _ = parse_envelope(text, "code-review")
    assert env.verdict == "PASS"
    with pytest.raises(ContractError):
        parse_envelope(envelope(kind="spec-evaluate"), "code-review")
    with pytest.raises(ContractError):
        parse_envelope(envelope(extra_field=1), "code-review")
    with pytest.raises(ContractError):
        parse_envelope("I approve this, PASS", "code-review")
    bad_loc = {"id": "F-1", "severity": "low", "blocking": False, "problem": "p", "location": {"path": "../../etc/passwd"}}
    with pytest.raises(ContractError):
        parse_envelope(envelope(findings=[bad_loc]), "code-review")
