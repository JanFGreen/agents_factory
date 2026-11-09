import json

import pytest

from agent_factory.collect import SourceUnavailable, from_sentry, run, scrub


def test_personal_data_is_scrubbed_before_anyone_reads_it():
    text = "Login failed for ada@example.com from 10.1.2.3 token=abc123 user 7d1f0c2e-1111-2222-3333-444455556666 tel +48 600 700 800"
    clean = scrub(text)
    assert "ada@" not in clean and "10.1.2.3" not in clean and "abc123" not in clean and "600 700" not in clean
    assert "<email>" in clean and "<ip>" in clean and "token=<redacted>" in clean and "<uuid>" in clean


def sentry(rows):
    def fetch(url, headers):
        assert headers["Authorization"] == "Bearer t" and "is%3Aunresolved" in url
        return json.dumps(rows)
    return fetch


def issue(i, last_seen, title="TypeError in ReviewScreen"):
    return {"id": str(i), "title": title, "culprit": "src/ui/ReviewScreen.tsx", "count": "12", "userCount": 3,
            "firstSeen": "2026-11-01T10:00:00Z", "lastSeen": last_seen, "permalink": f"https://sentry.test/{i}"}


def test_groups_are_sorted_into_new_ongoing_quiet_and_regressed_across_runs(tmp_path):
    release = "2026-11-05T00:00:00Z"
    first = run("sentry app", from_sentry("org", "app", "t", fetch=sentry([
        issue(1, "2026-11-06T09:00:00Z"), issue(2, "2026-11-03T09:00:00Z", "fetch timeout")])), release, tmp_path)
    assert first["counts"] == {"regressed": 0, "new": 1, "ongoing": 0, "quiet": 1}
    second = run("sentry app", from_sentry("org", "app", "t", fetch=sentry([
        issue(1, "2026-11-07T09:00:00Z"), issue(2, "2026-11-08T09:00:00Z", "fetch timeout")])), release, tmp_path)
    by_id = {g["fingerprint"]: g["status"] for g in second["groups"]}
    assert by_id == {"sentry:1": "ongoing", "sentry:2": "regressed"}
    text = (tmp_path / second["report"].rsplit("/", 1)[1]).read_text()
    assert text.index("## regressed") < text.index("## ongoing") and "untrusted" in text


def test_an_unreadable_source_is_never_reported_as_no_errors():
    def down(url, headers):
        raise OSError("connection refused")

    with pytest.raises(SourceUnavailable, match="sentry org/app"):
        from_sentry("org", "app", "t", fetch=down)
