"""Deterministic security signals on the diff: secret scan (blocks) and triggers (route the review)."""

from agent_factory.config import load_config
from agent_factory.policy import FileDiff, evaluate_diff, scan_secrets, security_triggers

from ..conftest import make_factory_dir

AWS = "AKIA" + "Q3VZ7RTXK2MNP4LD"  # assembled so this file is not itself a secret-shaped literal


def added(path: str, *lines: str, status: str = "A") -> FileDiff:
    return FileDiff(path, status, f"@@ -0,0 +1,{len(lines)} @@\n" + "".join(f"+{line}\n" for line in lines))


def test_key_shaped_literals_block_and_the_value_is_never_echoed():
    found = scan_secrets([added("app/aws.py", "import os", f'KEY = "{AWS}"'),
                          added("app/pk.ts", "const k = `-----BEGIN RSA PRIVATE KEY-----`")])
    assert found[0].startswith("possible secret (AWS access key) added in app/aws.py:2") and AWS not in found[0]
    assert "private key" in found[1] and "app/pk.ts:1" in found[1]


def test_secret_files_block_but_templates_do_not():
    found = scan_secrets([added(".env", "A=1"), added("deploy/server.pem", "x"), added(".env.example", "A="),
                          added("config/credentials.json", "{}")])
    assert found == ["secret file added: .env", "secret file added: deploy/server.pem", "secret file added: config/credentials.json"]


def test_hard_coded_credentials_need_a_random_looking_value_outside_tests():
    assert scan_secrets([added("src/db.ts", 'const password = "Zq8vR2mL0pXw4tY7"')])
    assert not scan_secrets([added("src/db.ts", 'const password = "changeme-changeme"')])
    assert not scan_secrets([added("src/db.ts", 'const apiKey = "process.env.API_KEY_VALUE"')])
    assert not scan_secrets([added("src/auth.test.ts", 'const password = "Zq8vR2mL0pXw4tY7"')])  # test fixture


def test_removed_lines_and_line_numbers_follow_the_hunk():
    diff = FileDiff("app/x.py", "M", f"@@ -10,2 +10,2 @@\n context\n-old = '{AWS}'\n+new = '{AWS}'\n")
    assert scan_secrets([diff]) == ["possible secret (AWS access key) added in app/x.py:11; use configuration, never a literal"]


def test_a_secret_fails_the_policy_gate(tmp_path):
    policy = load_config(make_factory_dir(tmp_path)).factory.policy
    report = evaluate_diff([added("app/aws.py", f'KEY = "{AWS}"')], policy)
    assert not report.ok and any("AWS access key" in v for v in report.violations)


def test_triggers_name_what_needs_a_threat_analysis(tmp_path):
    policy = load_config(make_factory_dir(tmp_path)).factory.policy
    triggers = security_triggers([
        added("src/api/routes.ts", "router.post('/login', async (req, res) => {", "  const r = await fetch(req.body.url)"),
        added("src/ui/Card.tsx", "<div dangerouslySetInnerHTML={{__html: html}} />"),
        added("package.json", "{}", status="M"),
        added("src/auth.test.ts", "eval(x)"),  # tests do not trigger
        added("docs/SECURITY.md", "never use eval("),
    ], policy)
    assert {(t["trigger"], t["path"]) for t in triggers} == {
        ("endpoint", "src/api/routes.ts"), ("auth", "src/api/routes.ts"), ("outbound-request", "src/api/routes.ts"),
        ("html-sink", "src/ui/Card.tsx"), ("dependency", "package.json")}


def test_a_game_session_is_not_an_auth_trigger(tmp_path):
    """Live finding (tic-tac-toe F1): `session` in a game's session reducer bought a needless full review."""
    policy = load_config(make_factory_dir(tmp_path)).factory.policy
    game = added("src/domain/session.ts", "export function sessionReducer(session: Session, action: Action): Session {")
    auth = added("src/server/auth.ts", "const sessionToken = req.cookies.sid")
    assert security_triggers([game], policy) == []
    assert ("auth", "src/server/auth.ts") in {(t["trigger"], t["path"]) for t in security_triggers([auth], policy)}
