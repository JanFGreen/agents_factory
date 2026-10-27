import pytest

from agent_factory.config import load_config
from agent_factory.policy import BrokerDenied, FileDiff, authorize_side_effect, evaluate_diff

from ..conftest import make_factory_dir


@pytest.fixture
def policy(tmp_path):
    return load_config(make_factory_dir(tmp_path)).factory.policy


def test_af11_auth_endpoint_in_ui_change_raises_risk(policy):
    diff = FileDiff("app/views.py", "M", '+@app.route("/admin")\n+def admin():\n+    token = request.args["token"]\n')
    report = evaluate_diff([diff], policy)
    assert report.risk == "high" and "security" in report.surfaces


def test_ui_surface_detected(policy):
    assert "ui" in evaluate_diff([FileDiff("web/Button.tsx", "M", "+x")], policy).surfaces


@pytest.mark.parametrize("diff,needle", [
    (FileDiff("tests/test_a.py", "M", "+@pytest.mark.skip\n"), "test weakening"),
    (FileDiff("tests/test_a.py", "M", "-    assert x == 1\n+    assert True\n"), "test weakening"),
    (FileDiff("tests/test_a.py", "M", "-def test_one():\n-    assert f()\n"), "test cases removed"),
    (FileDiff("tests/test_a.py", "D", ""), "test file deleted"),
    (FileDiff("conftest.py", "A", "+collect_ignore=['tests']"), "protected"),
    (FileDiff("package.json", "M", '+"test": "exit 0"'), "protected"),
    (FileDiff(".github/workflows/ci.yml", "M", "+x"), "protected"),
    (FileDiff("factory/checks/default.yaml", "M", "+x"), "protected"),
    (FileDiff("src/ui/Card.test.tsx", "M", "+  it.skip('flips', () => {\n"), "test weakening"),
    (FileDiff("src/domain/srs.test.ts", "M", "+describe.only('srs', () => {\n"), "test weakening"),
    (FileDiff("src/domain/srs.test.ts", "M", "-    expect(next.interval).toBe(6);\n+    expect(true).toBe(true);\n"), "test weakening"),
    (FileDiff("src/domain/srs.test.ts", "M", "-  it('grows', () => {\n-    expect(a).toBe(1);\n"), "test cases removed"),
    (FileDiff("src/ui/Card.test.tsx", "D", ""), "test file deleted"),
    (FileDiff("tsconfig.json", "M", '+"strict": false'), "protected"),
    (FileDiff("eslint.config.mjs", "M", "+rules: {}"), "protected"),
    (FileDiff("package-lock.json", "M", "+x"), "protected"),
])
def test_af13_weakening_and_protected_inputs(policy, diff, needle):
    report = evaluate_diff([diff], policy)
    assert any(needle in v for v in report.violations)


def test_file_scope_enforced(policy):
    report = evaluate_diff([FileDiff("app/other.py", "M", "+x")], policy, task_file_scope=["app/greet.py"])
    assert any("outside task file_scope" in v for v in report.violations)
    assert evaluate_diff([FileDiff("app/greet.py", "M", "+x")], policy, task_file_scope=["app/greet.py"]).ok


@pytest.mark.parametrize("path", ["src/ui/Card.test.tsx", "src/a.spec.ts", "src/b.test.mjs", "tests/helpers.ts", "app/test_x.py"])
def test_js_and_python_test_files_are_always_in_scope(policy, path):
    assert evaluate_diff([FileDiff(path, "A", "+x")], policy, task_file_scope=["src/domain/"]).ok


DEAD = [FileDiff("src/ui/WelcomeScreen.tsx", "D", "-export const W = 1;\n"),
        FileDiff("src/ui/WelcomeScreen.test.tsx", "D", "-it('renders', () => {\n-  expect(render()).toBeTruthy();\n")]


def test_dead_code_may_be_removed_with_its_test_when_the_task_names_it(policy):
    """Live finding (LearnEnglish F2): the plan said 'remove the placeholder screen and its test', the policy
    forbade any test deletion, and the task FAILED after 3 attempts."""
    scope = ["src/ui/WelcomeScreen.tsx", "src/ui/WelcomeScreen.test.tsx", "src/app/index.tsx"]
    assert evaluate_diff(DEAD, policy, task_file_scope=scope).ok


@pytest.mark.parametrize("files,scope", [
    (DEAD[1:], ["src/ui/WelcomeScreen.tsx", "src/ui/WelcomeScreen.test.tsx"]),  # subject still exists
    (DEAD, ["src/ui/WelcomeScreen.tsx"]),  # the task does not name the test
    (DEAD, None),  # no task scope at all
    ([FileDiff("src/ui/Other.tsx", "D", "-x\n"), DEAD[1]], ["src/ui/"]),  # a different module was removed
])
def test_test_deletion_without_its_subject_or_scope_is_still_refused(policy, files, scope):
    report = evaluate_diff(files, policy, task_file_scope=scope)
    assert any("test file deleted" in v for v in report.violations)


def test_python_test_goes_with_its_module(policy):
    files = [FileDiff("app/legacy.py", "D", "-x = 1\n"), FileDiff("tests/test_legacy.py", "D", "-def test_x():\n-    assert x\n")]
    assert evaluate_diff(files, policy, task_file_scope=["app/legacy.py", "tests/test_legacy.py"]).ok


def test_af40_local_mode_broker_denies_comments_and_deploy(policy):
    for op in ("github.comment", "github.create_pr", "github.merge", "deploy"):
        with pytest.raises(BrokerDenied):
            authorize_side_effect(policy, op)
    authorize_side_effect(policy, "local.merge")
