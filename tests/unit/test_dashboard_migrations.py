import json
import sqlite3
import threading
import urllib.request
from http.server import ThreadingHTTPServer

from agent_factory.dashboard import job_page, make_handler, render_text
from agent_factory.state import MIGRATIONS, Store


def seed(store):
    with store.tx() as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, feature_id, repo_id, source_id, scope_revision, state, repo_path, analysis_rounds_limit,"
            " budget_limit_usd, context_json, blocked_reason, created_at, updated_at) VALUES"
            " ('job-0000000000aa','F','r','s',1,'NEEDS_HUMAN','/x',3,10,?,?, 't','t')",
            (json.dumps({"intake": {"goal": "<img src=x onerror=alert(1)>"}}), "<script>alert(1)</script>"),
        )


def test_af38_markdown_and_html_are_not_executed(tmp_path):
    store = Store(tmp_path / "s.db")
    seed(store)
    page = job_page(store, "job-0000000000aa")
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "<img" not in page
    assert render_text("[x](javascript:alert(1))") == "<pre>[x](javascript:alert(1))</pre>"


def test_af38_dashboard_routes_are_whitelisted_and_read_only(tmp_path):
    store = Store(tmp_path / "s.db")
    seed(store)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(store))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_port}"
    try:
        with urllib.request.urlopen(base + "/job/job-0000000000aa") as resp:
            assert "default-src 'none'" in resp.headers["Content-Security-Policy"]
        for bad in ("/../../etc/passwd", "/job/../state.db", "/artifacts/keys"):
            try:
                urllib.request.urlopen(base + bad)
                raise AssertionError("should 404")
            except urllib.error.HTTPError as exc:
                assert exc.code == 404
        req = urllib.request.Request(base + "/job/job-0000000000aa", data=b"x", method="POST")
        try:
            urllib.request.urlopen(req)
        except urllib.error.HTTPError as exc:
            assert exc.code == 405
    finally:
        server.shutdown()


def test_schema_migration_from_v1(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    conn.executescript(MIGRATIONS[0][1])
    conn.execute("INSERT INTO schema_migrations VALUES (1, 't')")
    conn.commit()
    conn.close()
    store = Store(path)
    assert store.schema_version() == len(MIGRATIONS)
    cols = [r[1] for r in store.conn.execute("PRAGMA table_info(attempts)")]
    assert "handle_json" in cols


def test_backup_and_restore_roundtrip(tmp_path):
    store = Store(tmp_path / "s.db")
    seed(store)
    backup = sqlite3.connect(tmp_path / "backup.db")
    store.conn.backup(backup)
    backup.close()
    restored = Store(tmp_path / "backup.db")
    assert restored.job("job-0000000000aa")["state"] == "NEEDS_HUMAN"
