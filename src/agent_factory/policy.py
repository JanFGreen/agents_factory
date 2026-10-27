"""Policy gate on the actual diff, risk routing, and the side-effect broker.

Prompts describe behaviour; this module enforces it. The policy gate protects the
transitive inputs of evaluation (tests config, CI, package scripts, check manifests)
and detects test weakening. Risk is the maximum of every signal and can only rise.
"""

from __future__ import annotations

import fnmatch
import re
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

_WEAKENING = [
    re.compile(r"^\+.*(@pytest\.mark\.(skip|xfail)|pytest\.skip\(|unittest\.skip|\.skip\(|\.skipIf\(|\.only\(|\.todo\(|xit\(|xdescribe\()"),
    re.compile(r"^\+\s*assert\s+True\b"),
    re.compile(r"^\+\s*expect\(\s*(true|1)\s*\)\.(toBe|toEqual)\(\s*(true|1)\s*\)"),
]
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
        if is_test_file(diff.path):
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
        for level, pattern, label in _CONTENT_RULES:
            if pattern.search(added):
                report.risk = max_risk(report.risk, level)
                report.risk_reasons.append(f"{label} in {diff.path}")
                if level == "high":
                    report.surfaces.add("security")
        if re.search(r"\.(tsx|jsx|vue|svelte|html|css|scss)$", diff.path) or "/templates/" in f"/{diff.path}":
            report.surfaces.add("ui")
    return report


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
