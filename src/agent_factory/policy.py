"""Policy gate on the actual diff, risk routing, and the side-effect broker.

Prompts describe behaviour; this module enforces it. The policy gate protects the
transitive inputs of evaluation (tests config, CI, package scripts, check manifests)
and detects test weakening. Risk is the maximum of every signal and can only rise.
"""

from __future__ import annotations

import fnmatch
import math
import re
from collections import Counter
from dataclasses import dataclass, field

from .config import Policy

RISK_ORDER = {"low": 0, "medium": 1, "high": 2}

# Signals that raise risk regardless of what intake called the change.
_CONTENT_RULES: list[tuple[str, re.Pattern[str], str]] = [
    ("high", re.compile(r"\b(password|passwd|secret|token|api[_-]?key|credential|jwt|oauth|session)\b", re.I), "credential/auth handling"),
    ("high", re.compile(r"@(app|router|bp)\.(route|get|post|put|delete|patch)\b|\bAPIRouter\b|\bexpress\.Router\b"), "new or changed HTTP endpoint"),
    ("high", re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b.+\b(FROM|INTO|SET)\b|execute\(\s*f[\"']", re.I), "SQL"),
    ("high", re.compile(r"\bwebhook\b", re.I), "webhook"),
    ("medium", re.compile(r"\bsubprocess\b|\bos\.system\b|\beval\(|\bexec\("), "process execution / dynamic code"),
]

# Credentials in added lines (high-confidence shapes only). The violation names the kind and the line, never the value.
_SECRET_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("private key", re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY")),
    ("AWS access key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{60,}\b")),
    ("OpenAI/Anthropic API key", re.compile(r"\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{32,}")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Slack token", re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}")),
    ("Stripe live key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{20,}")),
    ("JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
]
# `password = "..."`-style literals: outside tests only, and only when the value looks random.
_SECRET_ASSIGNMENT = re.compile(
    r"\b(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token|client[_-]?secret)\w*[\"']?\s*[:=]\s*[\"']([^\"'\s]{16,})[\"']", re.I)
_PLACEHOLDER = re.compile(r"example|changeme|placeholder|dummy|sample|your[_-]|xxx|\*\*\*|<|\$\{|\{\{|process\.env|os\.environ", re.I)
_SECRET_FILES = re.compile(r"(^|/)(\.env(\.[\w-]+)?|[^/]*\.(pem|key|p12|pfx|jks|keystore)|id_(rsa|ed25519|ecdsa)|credentials\.json|service-account[^/]*\.json)$")
_SECRET_FILE_TEMPLATES = re.compile(r"\.env\.(example|sample|template)$")

# What makes a change worth a full threat analysis (reported to the security reviewer and used to route it).
_DEPENDENCY_FILES = re.compile(r"(^|/)(package(-lock)?\.json|pnpm-lock\.yaml|yarn\.lock|requirements[^/]*\.txt|pyproject\.toml|uv\.lock|poetry\.lock|"
                               r"Cargo\.(toml|lock)|go\.(mod|sum)|pom\.xml|build\.gradle(\.kts)?|Gemfile(\.lock)?)$")
_TRIGGER_RULES: list[tuple[str, re.Pattern[str]]] = [
    # `session` alone is too common (a game or study session); only its auth forms count.
    ("auth", re.compile(r"\b(password|passwd|login|logout|sign[_-]?in|jwt|oauth|bcrypt|argon2|authori[sz]|permission)\w*|"
                        r"\b(session[_-]?(id|token|cookie|secret)|req\.session|express-session|set-cookie)\b", re.I)),
    ("secrets", re.compile(r"\b(secret|api[_-]?key|access[_-]?token|credential|private[_-]?key)\w*|process\.env|os\.environ|getenv\(", re.I)),
    ("endpoint", re.compile(r"@(app|router|bp)\.(route|get|post|put|delete|patch)\b|\bAPIRouter\b|\bexpress\.Router\b|"
                            r"\b(app|router)\.(get|post|put|delete|patch|use)\(|\bcreateServer\(|\bexport\s+(async\s+)?function\s+(GET|POST|PUT|DELETE|PATCH)\b")),
    ("sql", re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE)\b.+\b(FROM|INTO|SET)\b|execute\(\s*f[\"']|\.raw\(|\$queryRaw", re.I)),
    ("outbound-request", re.compile(r"\bfetch\(|\baxios\b|\brequests\.(get|post|put|delete|request)\(|\burllib\.request\b|\bhttpx\.|\bhttps?\.request\(")),
    ("html-sink", re.compile(r"dangerouslySetInnerHTML|\.innerHTML\s*=|\bv-html\b|\|\s*safe\b|mark_safe\(")),
    ("process-exec", re.compile(r"\bsubprocess\b|\bos\.system\b|\bchild_process\b|\beval\(|\bexec\(|new Function\(")),
    ("file-upload", re.compile(r"\bmulter\b|\bmultipart\b|\bUploadFile\b|\bFormData\b|\bupload\w*\(", re.I)),
    ("webhook", re.compile(r"\bwebhook\b", re.I)),
    ("crypto", re.compile(r"\bcreateHash\(|\bcreateCipheriv\(|\bhashlib\.|\bMath\.random\(\)|\brandom\.(random|randint)\(")),
]

# Operations whose failure modes (timeout, retry, lost response, double submit, unmount) need an explicit
# analysis in code review: network calls, request handlers, persistent writes, browser storage, timers.
_EFFECT_RULES: list[tuple[str, re.Pattern[str]]] = [
    ("network-call", re.compile(r"\bfetch\(|\baxios\b|\bXMLHttpRequest\b|\bnew WebSocket\(|\bEventSource\(|"
                                r"\brequests\.(get|post|put|patch|delete|request)\(|\bhttpx\.|\burllib\.request\b|\baiohttp\b")),
    ("request-handler", re.compile(r"@(app|router|bp)\.(route|get|post|put|delete|patch)\b|\b(app|router|fastify)\.(get|post|put|patch|delete)\(|"
                                   r"\bexport\s+(async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE)\b")),
    ("persistent-write", re.compile(r"\b(INSERT|UPDATE|DELETE)\b\s+(INTO|\w+\s+SET|FROM)\b|\.(save|insert|upsert|update|delete|create)\w*\(\s*\w|"
                                    r"\bwriteFile(Sync)?\(|\.commit\(\)", re.I)),
    ("browser-storage", re.compile(r"\b(localStorage|sessionStorage|indexedDB)\b")),
    ("timer", re.compile(r"\bset(Timeout|Interval)\(|\basyncio\.sleep\(")),
]

_WEAKENING = [
    re.compile(r"^\+.*(@pytest\.mark\.(skip|xfail)|pytest\.skip\(|unittest\.skip|\.skip\(|\.skipIf\(|\.only\(|\.todo\(|xit\(|xdescribe\()"),
    re.compile(r"^\+\s*assert\s+True\b"),
    re.compile(r"^\+\s*expect\(\s*(true|1)\s*\)\.(toBe|toEqual)\(\s*(true|1)\s*\)"),
]
# Silencing a quality check instead of restructuring the code (engineering principles EP-1/3/4/12).
_LIMIT_RULES = r"complexity|max-[a-z-]+|no-restricted-imports|no-restricted-paths|@typescript-eslint/no-deprecated|import/no-cycle"
_SUPPRESSION = [
    re.compile(r"eslint-disable(?!-next-line|-line)(?:\s|\*/|$)"),  # block or whole-file disable
    re.compile(rf"eslint-disable(?:-next-line|-line)?\b.*(?<![\w/-])({_LIMIT_RULES})(?![\w-])"),
    re.compile(r"@ts-(ignore|nocheck)\b"),
    re.compile(r"jscpd:ignore"),
    re.compile(r"#\s*noqa\s*$|#\s*noqa:.*\b(C901|PLR09\d\d)\b"),  # bare noqa silences every rule
    re.compile(r"#\[allow\(clippy::(cognitive_complexity|too_many_arguments|too_many_lines)"),
    re.compile(r"CHECKSTYLE:OFF|@SuppressWarnings\(\s*\"(PMD|checkstyle)"),
]
_DOCS = re.compile(r"\.(md|mdx|rst|txt|adoc)$")  # prose may name a suppression without using one
_REMOVED_TEST = re.compile(r"^-\s*(def|async def)\s+test_\w+|^-\s*(it|test)\(")
_REMOVED_ASSERT = re.compile(r"^-\s*(assert\b|self\.assert|expect\()")
_ADDED_ASSERT = re.compile(r"^\+\s*(assert\b|self\.assert|expect\()")


@dataclass
class FileDiff:
    path: str
    status: str  # A, M, D, R
    patch: str


@dataclass
class PolicyReport:
    violations: list[str] = field(default_factory=list)
    risk: str = "low"
    risk_reasons: list[str] = field(default_factory=list)
    surfaces: set[str] = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return not self.violations


def max_risk(*levels: str) -> str:
    return max(levels, key=lambda level: RISK_ORDER[level])


def _matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


_JS_TEST = re.compile(r"\.(test|spec)\.[cm]?[jt]sx?$")


def is_test_file(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return name.startswith("test_") or name.endswith("_test.py") or bool(_JS_TEST.search(name)) or "/tests/" in f"/{path}"


def _test_subject(path: str) -> tuple[str, str] | None:
    """(directory, stem) of the module a test file covers: ``ui/Card.test.tsx`` -> ("ui", "Card")."""
    folder, _, name = path.rpartition("/")
    match = _JS_TEST.search(name)
    if match:
        return folder, name[: match.start()]
    if name.startswith("test_") and name.endswith(".py"):
        return "", name[len("test_"):-3]
    return None


def _removed_with_its_subject(diff: "FileDiff", files: list["FileDiff"], task_file_scope: list[str] | None) -> bool:
    """A test may go only together with the code it tests, and only when the task names it in its scope."""
    if diff.status != "D" or not task_file_scope or not _matches(diff.path, [*task_file_scope, *[f"{s.rstrip('/')}/*" for s in task_file_scope]]):
        return False
    subject = _test_subject(diff.path)
    if subject is None:
        return False
    folder, stem = subject
    for other in files:
        if other.status != "D" or is_test_file(other.path):
            continue
        other_folder, _, other_name = other.path.rpartition("/")
        if other_name.rsplit(".", 1)[0] == stem and (not folder or other_folder == folder):
            return True
    return False


def is_ui_path(path: str) -> bool:
    """A user-visible file: components, markup, styles or server templates (tests excluded)."""
    return not is_test_file(path) and (bool(re.search(r"\.(tsx|jsx|vue|svelte|html|css|scss)$", path)) or "/templates/" in f"/{path}")


def evaluate_diff(
    files: list[FileDiff],
    policy: Policy,
    *,
    task_file_scope: list[str] | None = None,
    allow_test_changes: bool = True,
) -> PolicyReport:
    report = PolicyReport()
    for diff in files:
        if _matches(diff.path, policy.protected_paths):
            report.violations.append(f"protected evaluation input changed: {diff.path}")
        if task_file_scope and not _matches(diff.path, [*task_file_scope, *[f"{s.rstrip('/')}/*" for s in task_file_scope]]):
            if not is_test_file(diff.path):
                report.violations.append(f"change outside task file_scope: {diff.path}")
        if is_test_file(diff.path) and _removed_with_its_subject(diff, files, task_file_scope):
            pass  # dead code removed together with its test (the task's scope names the test)
        elif is_test_file(diff.path):
            if diff.status == "D":
                report.violations.append(f"test file deleted: {diff.path}")
            lines = diff.patch.splitlines()
            for line in lines:
                if any(p.search(line) for p in _WEAKENING):
                    report.violations.append(f"test weakening in {diff.path}: {line[:120]}")
            removed_tests = sum(1 for line in lines if _REMOVED_TEST.search(line))
            added_tests = sum(1 for line in lines if line.startswith("+") and re.search(r"def test_\w+|\b(it|test)\(", line))
            if removed_tests > added_tests:
                report.violations.append(f"test cases removed in {diff.path} ({removed_tests} removed, {added_tests} added)")
            removed_asserts = sum(1 for line in lines if _REMOVED_ASSERT.search(line))
            added_asserts = sum(1 for line in lines if _ADDED_ASSERT.search(line))
            if removed_asserts > added_asserts:
                report.violations.append(f"assertions removed in {diff.path} ({removed_asserts} removed, {added_asserts} added)")
            if not allow_test_changes:
                report.violations.append(f"task is not allowed to change tests: {diff.path}")
        for level, patterns in policy.risk_path_rules.items():
            if _matches(diff.path, patterns):
                report.risk = max_risk(report.risk, level)
                report.risk_reasons.append(f"path rule {level}: {diff.path}")
        added = "\n".join(line[1:] for line in diff.patch.splitlines() if line.startswith("+") and not line.startswith("+++"))
        for line in ([] if _DOCS.search(diff.path) else added.splitlines()):
            if any(p.search(line) for p in _SUPPRESSION):
                report.violations.append(f"quality check suppressed in {diff.path}: {line.strip()[:120]}")
        for level, pattern, label in _CONTENT_RULES:
            if pattern.search(added):
                report.risk = max_risk(report.risk, level)
                report.risk_reasons.append(f"{label} in {diff.path}")
                if level == "high":
                    report.surfaces.add("security")
        if is_ui_path(diff.path):
            report.surfaces.add("ui")
    report.violations += scan_secrets(files)
    return report


def _entropy(value: str) -> float:
    return -sum((n / len(value)) * math.log2(n / len(value)) for n in Counter(value).values())


def scan_secrets(files: list[FileDiff]) -> list[str]:
    """Credentials a change adds: secret files and key-shaped literals. Fail-closed; the value is never echoed."""
    found = []
    for diff in files:
        if diff.status != "D" and _SECRET_FILES.search(diff.path) and not _SECRET_FILE_TEMPLATES.search(diff.path):
            found.append(f"secret file added: {diff.path}")
            continue
        line_no = 0
        for line in diff.patch.splitlines():
            if line.startswith("@@"):
                m = re.search(r"\+(\d+)", line)
                line_no = int(m.group(1)) - 1 if m else 0
                continue
            if line.startswith("-") or line.startswith("+++"):
                continue
            line_no += 1
            if not line.startswith("+"):
                continue
            text = line[1:]
            kinds = [kind for kind, pattern in _SECRET_RULES if pattern.search(text)]
            assigned = None if is_test_file(diff.path) else _SECRET_ASSIGNMENT.search(text)
            if assigned and not _PLACEHOLDER.search(assigned.group(1)) and _entropy(assigned.group(1)) >= 3.5:
                kinds.append("hard-coded credential")
            if kinds:
                found.append(f"possible secret ({', '.join(kinds)}) added in {diff.path}:{line_no}; use configuration, never a literal")
    return found


def effect_sites(files: list[FileDiff]) -> list[dict]:
    """Production files whose added lines call the network, handle requests, persist data, use storage or timers."""
    sites = []
    for diff in files:
        if diff.status == "D" or is_test_file(diff.path) or _DOCS.search(diff.path):
            continue
        added = "\n".join(line[1:] for line in diff.patch.splitlines() if line.startswith("+") and not line.startswith("+++"))
        kinds = [name for name, pattern in _EFFECT_RULES if pattern.search(added)]
        if kinds:
            sites.append({"path": diff.path, "kinds": kinds})
    return sites


def security_triggers(files: list[FileDiff], policy: Policy) -> list[dict[str, str]]:
    """Signals in the actual diff that call for a full threat analysis: one entry per (trigger, path)."""
    found: dict[tuple[str, str], None] = {}
    high_paths = policy.risk_path_rules.get("high", [])
    for diff in files:
        if _DEPENDENCY_FILES.search(diff.path):
            found[("dependency", diff.path)] = None
        if is_test_file(diff.path) or _DOCS.search(diff.path):
            continue
        if _matches(diff.path, high_paths):
            found[("sensitive-path", diff.path)] = None
        added = "\n".join(line[1:] for line in diff.patch.splitlines() if line.startswith("+") and not line.startswith("+++"))
        for name, pattern in _TRIGGER_RULES:
            if pattern.search(added):
                found[(name, diff.path)] = None
    return [{"trigger": t, "path": p} for t, p in found]


class BrokerDenied(PermissionError):
    pass


SIDE_EFFECTS = {
    "github.comment": ("external_messages", {"pr", "automerge", "release"}),
    "github.create_pr": (None, {"pr", "automerge", "release"}),
    "github.merge": (None, {"automerge", "release"}),
    "local.merge": (None, {"local", "pr", "automerge", "release"}),
    "deploy": ("deploy", {"release"}),
}


def authorize_side_effect(policy: Policy, operation: str) -> None:
    """Deterministic broker check. Content of issues/logs never grants permissions."""
    if operation not in SIDE_EFFECTS:
        raise BrokerDenied(f"unknown side effect {operation!r}")
    flag, modes = SIDE_EFFECTS[operation]
    if policy.mode not in modes:
        raise BrokerDenied(f"{operation} not allowed in mode {policy.mode!r}")
    if flag is not None and not getattr(policy, flag):
        raise BrokerDenied(f"{operation} requires policy.{flag}=true")
