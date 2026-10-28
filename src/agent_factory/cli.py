"""``factory`` command line. Every mutation validates arguments, writes an audit event
and supports ``--json``. Exit codes: 0 ok, 2 usage error, 3 refused, 4 blocked/halted."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import yaml

from . import analysis, budgets
from .adapters.claude import ClaudeRuntime
from .adapters.codex import CodexRuntime
from .adapters.opencode import OpencodeRuntime
from .adapters.router import RuntimeRouter
from .adapters.github import LocalMergeHandler
from .config import IMPLEMENTED_RUNTIMES, ConfigError, load_config
from .controller import HALTED, TERMINAL, TransitionRefused
from .environment import cli_too_old, node_bin_dir
from .features import normalize
from .sandbox import SandboxUnavailable, get_sandbox
from .state import Store

EXIT_OK, EXIT_USAGE, EXIT_REFUSED, EXIT_BLOCKED = 0, 2, 3, 4


def _out(args, data: Any, text: str | None = None) -> None:
    if args.json:
        print(json.dumps(data, indent=2, default=str, ensure_ascii=False))
    else:
        print(text if text is not None else json.dumps(data, indent=2, default=str, ensure_ascii=False))


def _factory_dir(args) -> Path:
    return Path(args.factory_dir or os.environ.get("AGENT_FACTORY_DIR") or "factory")


def _load(args):
    cfg = load_config(_factory_dir(args))
    if args.data_dir:
        cfg = cfg.model_copy(update={"factory": cfg.factory.model_copy(update={"data_dir": args.data_dir})})
    store = Store(cfg.data_dir() / "state.db")
    return cfg, store


def _repos_file(cfg) -> Path:
    return cfg.data_dir() / "repos.json"


def _repos(cfg) -> dict[str, dict]:
    path = _repos_file(cfg)
    return json.loads(path.read_text()) if path.exists() else {}


def _binary(cfg, binary: str) -> str:
    """A runtime binary from models.yaml: a command on PATH, or a path relative to the factory checkout."""
    return str((cfg.factory_dir.parent / binary).absolute()) if "/" in binary else binary


def _make_runtime(cfg, sandbox):
    """One adapter per runtime defined in models.yaml, behind a router that follows each role's binding."""
    logs = cfg.data_dir() / "agent-logs"
    rt = cfg.factory.runtime
    runtimes = {"claude": ClaudeRuntime(rt.claude_binary, sandbox, logs)}
    for name, rdef in rt.runtimes.items():
        if name == "codex":
            runtimes[name] = CodexRuntime(_binary(cfg, rdef.binary), sandbox, logs, rdef.pass_env,
                                          {model: price.model_dump() for model, price in rt.prices.items()})
        elif name == "opencode":
            runtimes[name] = OpencodeRuntime(_binary(cfg, rdef.binary), sandbox, logs, rdef.pass_env)
    return RuntimeRouter(runtimes)


def _make_factory(cfg, store):
    from .factory import Factory

    sandbox = get_sandbox()
    runtime = _make_runtime(cfg, sandbox)
    handler = LocalMergeHandler(cfg.factory.policy) if cfg.factory.policy.mode == "local" else None
    return Factory(cfg, store, runtime, sandbox, merge_handler=handler)


# --- commands ---------------------------------------------------------------------------


def cmd_doctor(args) -> int:
    report: dict[str, Any] = {}
    ok = True
    try:
        cfg, store = _load(args)
        report["config"] = {"ok": True, "policy_digest": cfg.policy_digest, "data_dir": str(cfg.data_dir()), "mode": cfg.factory.policy.mode}
        report["database"] = {"path": str(store.path), "schema_version": store.schema_version()}
    except ConfigError as exc:
        _out(args, {"config": {"ok": False, "error": str(exc)}})
        return EXIT_USAGE
    try:
        sandbox = get_sandbox()
        report["sandbox"] = {"ok": True, "name": sandbox.name, "probe": "write outside and secret read denied"}
    except SandboxUnavailable as exc:
        ok = False
        report["sandbox"] = {"ok": False, "error": str(exc), "effect": "code execution refused (fail closed)"}
    claude = shutil.which(cfg.factory.runtime.claude_binary)
    too_old = cli_too_old(claude, cfg.factory.runtime.min_cli_version) if claude else "claude CLI not found"
    report["claude_cli"] = {"path": claude, "version": subprocess.run([claude, "--version"], capture_output=True, text=True).stdout.strip() if claude else None,
                            "min_version": cfg.factory.runtime.min_cli_version, "ok": too_old is None, **({"error": too_old} if too_old else {})}
    ok &= too_old is None
    toolchains = {}
    for repo_id, repo in _repos(cfg).items():
        try:
            node = node_bin_dir(Path(repo["path"]))
            toolchains[repo_id] = {"node": str(node) if node else "not pinned (controller PATH)"}
        except FileNotFoundError as exc:
            ok = False
            toolchains[repo_id] = {"error": str(exc)}
    report["toolchains"] = toolchains
    lock = Path("factory.lock.json")
    report["lock"] = json.loads(lock.read_text()) if lock.exists() else None
    if cfg.factory.browser is not None:
        browser_bin = (cfg.factory_dir.parent / cfg.factory.browser.binary).absolute()
        report["browser"] = {"tool": cfg.factory.browser.tool, "binary": str(browser_bin), "present": browser_bin.exists(),
                             "viewports": cfg.factory.browser.viewports,
                             **({} if browser_bin.exists() else {"effect": "UI review falls back to reading code (local-checkout)",
                                                                 "fix": "cd tools/playwright-cli && npm ci"})}
    openspec_bin = Path("tools/openspec/node_modules/.bin/openspec")
    report["openspec"] = {"binary": str(openspec_bin), "present": openspec_bin.exists()}
    skills = sorted(p.name for p in (_factory_dir(args) / "skills").iterdir() if p.is_dir() and not p.name.startswith("_"))
    report["skills"] = {"count": len(skills), "names": skills}
    report["repos"] = _repos(cfg)
    dm = cfg.factory.decision_model
    report["decision_model"] = {
        "provider": dm.provider, "model": dm.model,
        "active": dm.provider != "none" and bool(os.environ.get(dm.api_key_env)),
        "note": "skipped (optional)" if dm.provider == "none" or not os.environ.get(dm.api_key_env) else f"task difficulty second opinion via {dm.url}",
    }
    report["routing"] = {"builder_models": cfg.factory.routing.builder_models, "escalate_on_retry": cfg.factory.routing.escalate_on_retry}
    roles_by_runtime: dict[str, list[str]] = {}
    for role, binding in cfg.factory.runtime.roles.items():
        roles_by_runtime.setdefault(binding.runtime, []).append(role)
    report["runtimes"] = {}
    for name, rdef in cfg.factory.runtime.runtimes.items():
        binary = _binary(cfg, rdef.binary)
        installed = Path(binary).exists() if "/" in binary else shutil.which(binary) is not None
        report["runtimes"][name] = {
            "implemented": name in IMPLEMENTED_RUNTIMES, "binary": binary, "installed": installed,
            "env": {target: ("set" if os.environ.get(source) else f"{source} not set") for target, source in rdef.pass_env.items()},
            "roles": roles_by_runtime.get(name, []),
        }
        if roles_by_runtime.get(name) and not installed:
            ok = False
    report["endpoints"] = {}
    for name, endpoint in cfg.factory.runtime.endpoints.items():
        try:
            with urllib.request.urlopen(endpoint.base_url.rstrip("/") + "/models", timeout=3) as response:
                served = {m.get("id") for m in json.loads(response.read()).get("data", [])}
            status = {"reachable": True, "missing_models": sorted(set(endpoint.models) - served)}
        except (OSError, ValueError) as exc:
            status = {"reachable": False, "error": str(exc)[:200]}
        host = urllib.parse.urlparse(endpoint.base_url).hostname or ""
        if endpoint.base_url.startswith("http://") and host not in ("127.0.0.1", "localhost", "::1"):
            status["warning"] = "plain http to another machine: code and prompts cross the network unencrypted (use TLS or a VPN such as Tailscale)"
        report["endpoints"][name] = {"base_url": endpoint.base_url, "max_concurrent": endpoint.max_concurrent, **status}
    report["credentials"] = {
        "claude": "CLI auth (keychain/config); checked by a live call, not by env",
        "github": "gh auth (needed only for mode pr/automerge)" if shutil.which("gh") else "gh not installed",
    }
    _out(args, report)
    return EXIT_OK if ok else EXIT_BLOCKED


def cmd_init(args) -> int:
    cfg, store = _load(args)
    repo = Path(args.repo).resolve()
    if not (repo / ".git").exists():
        print(f"{repo} is not a git repository", file=sys.stderr)
        return EXIT_USAGE
    if args.mode != cfg.factory.policy.mode:
        print(f"--mode {args.mode} differs from policy.mode {cfg.factory.policy.mode}; change factory/config.yaml as operator", file=sys.stderr)
        return EXIT_REFUSED
    repos = _repos(cfg)
    repo_id = args.repo_id or repo.name
    if args.checks and not (cfg.factory_dir / "checks" / f"{args.checks}.yaml").exists():
        print(f"unknown checks manifest {args.checks!r} (expected factory/checks/{args.checks}.yaml)", file=sys.stderr)
        return EXIT_USAGE
    repos[repo_id] = {"path": str(repo), "base_branch": args.base_branch, "mode": args.mode, "checks": args.checks}
    _repos_file(cfg).parent.mkdir(parents=True, exist_ok=True)
    _repos_file(cfg).write_text(json.dumps(repos, indent=2))
    with store.tx() as conn:
        store.event(conn, None, "repo.registered", {"repo_id": repo_id, "path": str(repo), "mode": args.mode})
    _out(args, {"repo_id": repo_id, **repos[repo_id]}, f"registered {repo_id} -> {repo}")
    return EXIT_OK


def _resolve_repo(cfg, repo_id: str | None) -> tuple[str, dict]:
    repos = _repos(cfg)
    if repo_id:
        if repo_id not in repos:
            raise SystemExit(f"unknown repo {repo_id}; run `factory init` first")
        return repo_id, repos[repo_id]
    if len(repos) != 1:
        raise SystemExit("specify --repo-id (zero or several repos registered)")
    return next(iter(repos.items()))


def cmd_submit(args) -> int:
    cfg, store = _load(args)
    repo_id, repo = _resolve_repo(cfg, args.repo_id)
    if args.issue is not None:
        print("GitHub issue intake requires policy.mode pr/automerge and a configured GitHub adapter", file=sys.stderr)
        if cfg.factory.policy.mode in ("offline", "local"):
            return EXIT_REFUSED
    if args.file:
        raw = yaml.safe_load(Path(args.file).read_text())
        feature = normalize(raw, source_kind="file", repo_id=repo_id, source_id=f"file:{Path(args.file).resolve()}")
    elif args.text:
        feature = normalize(args.text, source_kind="cli", repo_id=repo_id)
    else:
        print("--file or --text is required", file=sys.stderr)
        return EXIT_USAGE
    factory = _make_factory(cfg, store)
    job_id = factory.submit(feature, Path(repo["path"]), checks=repo.get("checks"), mode=_spec_mode(args, cfg))
    if repo.get("base_branch"):
        store.update_job_context(job_id, base_branch=repo["base_branch"])
    _out(args, {"job_id": job_id, "feature_id": feature.feature_id}, job_id)
    return EXIT_OK


def _spec_mode(args, cfg) -> str:
    """--mode, else ask at a terminal, else the configured default."""
    default = cfg.factory.spec.default_mode
    if args.mode or args.json or not sys.stdin.isatty():
        return args.mode or default
    print("Specification mode:\n  guided - you answer a few product questions after intake, then it runs on its own\n"
          "  auto   - the factory assumes the proposed answers and does not stop")
    while True:
        answer = input(f"Mode [guided/auto] ({default}): ").strip().lower() or default
        if answer in ("guided", "auto", "g", "a"):
            return {"g": "guided", "a": "auto"}.get(answer, answer)


def _pending_decisions(ctx: dict) -> list[dict]:
    return [d for d in ctx.get("decisions", []) if d.get("answer") is None]


def _status_row(store: Store, job_id: str) -> dict:
    job = store.job(job_id)
    rs = analysis.round_state(store, job_id)
    bud = budgets.status(store, job_id)
    ctx = json.loads(job["context_json"])
    return {
        "job_id": job_id, "feature_id": job["feature_id"], "state": job["state"], "path": job["path"], "risk": job["risk"],
        "blocked_reason": job["blocked_reason"], "head_sha": job["head_sha"], "base_sha": job["base_sha"],
        "analysis_rounds": f"{rs.used}/{rs.limit}", "build_attempts": job["build_attempts"],
        "budget": {"limit": bud.limit, "committed": round(bud.committed, 4), "spent": round(bud.spent, 4)},
        "tasks": [{**dict(r), "difficulty": ((ctx.get("task_difficulty") or {}).get("tasks", {}).get(r["task_id"]) or {}).get("difficulty")}
                  for r in store.all("SELECT task_id, state, attempts FROM tasks WHERE job_id = ? ORDER BY order_idx", (job_id,))],
        "last_checkpoint": ctx.get("last_checkpoint"),
        "plan_estimate": {k: ctx["plan_estimate"][k] for k in ("tasks", "estimate_usd", "suggested_limit_usd", "basis")}
        if ctx.get("plan_estimate") else None,
        "plan_approved": bool(ctx.get("plan_approval")),
        "spec_mode": ctx.get("spec_mode"),
        "decisions": [{k: d.get(k) for k in ("id", "question", "proposed", "answer", "source")} for d in ctx.get("decisions", [])],
        "next_step": f"answer the product decisions: `factory decide {job_id}`" if _pending_decisions(ctx) else _next_step(job["state"]),
        "criteria_digest": _criteria(store, job_id) if job["state"] == "AWAITING_HUMAN_MERGE" else None,
    }


def _criteria(store: Store, job_id: str) -> str | None:
    """The candidate digest a human decision must quote (recorded when the job started waiting)."""
    for event in reversed(store.events_for(job_id)):
        if event["to_state"] == "AWAITING_HUMAN_MERGE":
            return json.loads(event["payload_json"]).get("reason", "").removeprefix("criteria ")
    return None


def _next_step(state: str) -> str:
    return {
        "ANALYSIS_LIMIT_REACHED": "inspect findings (`factory feature inspect-analysis`), then raise the limit with a reason or reject",
        "BUDGET_EXHAUSTED": "raise the budget with a reason, then `factory resume`",
        "BLOCKED_ENV": "fix the environment named in blocked_reason, then `factory resume`",
        "NEEDS_INFO": "provide the missing information as a new feature revision",
        "NEEDS_HUMAN": "read blocked_reason; `factory resume` or `factory cancel`",
        "AWAITING_HUMAN_MERGE": "`factory approve|reject|rework <id> --revision <criteria digest>`",
        "READY_TO_MERGE": "locally accepted; merge is disabled (merge.enabled=false)",
    }.get(state, "`factory run --job <id>`")


def cmd_status(args) -> int:
    cfg, store = _load(args)
    if args.job:
        _out(args, _status_row(store, args.job))
    else:
        _out(args, [_status_row(store, r["job_id"]) for r in store.all("SELECT job_id FROM jobs ORDER BY created_at")])
    return EXIT_OK


def cmd_inspect(args) -> int:
    cfg, store = _load(args)
    row = _status_row(store, args.id)
    row["events"] = [dict(e) for e in store.events_for(args.id)][-50:]
    row["findings"] = [dict(f) for f in store.all("SELECT * FROM findings WHERE job_id = ?", (args.id,))]
    row["results"] = [dict(r) for r in store.all("SELECT result_id, kind, task_id, verdict, created_at FROM results WHERE job_id = ? ORDER BY rowid", (args.id,))]
    _out(args, row)
    return EXIT_OK


def _preflight(cfg) -> str | None:
    """Refuse to start agents that would fail anyway (a too old CLI wastes every call)."""
    binary = shutil.which(cfg.factory.runtime.claude_binary)
    return cli_too_old(binary, cfg.factory.runtime.min_cli_version) if binary else "claude CLI not found"


def cmd_run(args) -> int:
    cfg, store = _load(args)
    if (problem := _preflight(cfg)) is not None:
        print(problem, file=sys.stderr)
        return EXIT_BLOCKED
    factory = _make_factory(cfg, store)
    jobs = [args.job] if args.job else [r["job_id"] for r in store.all("SELECT job_id FROM jobs WHERE active = 1 ORDER BY created_at LIMIT ?", (args.max_jobs,))]
    results = {job_id: factory.run(job_id) for job_id in jobs}
    _out(args, results)
    return EXIT_BLOCKED if any(s in {str(h) for h in HALTED} for s in results.values()) else EXIT_OK


def cmd_resume(args) -> int:
    cfg, store = _load(args)
    if (problem := _preflight(cfg)) is not None:
        print(problem, file=sys.stderr)
        return EXIT_BLOCKED
    factory = _make_factory(cfg, store)
    factory.resume(args.id)
    _out(args, {"job_id": args.id, "state": factory.run(args.id)})
    return EXIT_OK


def cmd_cancel(args) -> int:
    cfg, store = _load(args)
    _out(args, {"job_id": args.id, "state": _make_factory(cfg, store).cancel(args.id)})
    return EXIT_OK


def _decide(args, decision: str, reason: str | None) -> int:
    cfg, store = _load(args)
    factory = _make_factory(cfg, store)
    try:
        factory.decide(args.id, decision, args.revision, actor=os.environ.get("USER", "operator"), reason=reason)
    except TransitionRefused as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_REFUSED
    _out(args, {"job_id": args.id, "decision": decision, "state": factory.run(args.id)})
    return EXIT_OK


def cmd_approve(args) -> int:
    return _decide(args, "approve", None)


def cmd_reject(args) -> int:
    return _decide(args, "reject", args.reason)


def cmd_rework(args) -> int:
    cfg, store = _load(args)
    feedback = Path(args.file).read_text()
    return _decide(args, "rework", feedback)


def cmd_verify(args) -> int:
    """Re-verify every stored result's signature and the artifacts' hashes."""
    cfg, store = _load(args)
    factory = _make_factory(cfg, store)
    problems = []
    for row in store.all("SELECT * FROM results WHERE job_id = ?", (args.id,)):
        try:
            factory.evidence._row_to_result(row)
        except Exception as exc:  # noqa: BLE001 - reported, not swallowed
            problems.append(str(exc))
    _out(args, {"job_id": args.id, "results_checked": len(store.all("SELECT 1 FROM results WHERE job_id = ?", (args.id,))), "problems": problems})
    return EXIT_OK if not problems else EXIT_BLOCKED


def cmd_recover(args) -> int:
    from .recovery import recover_all

    cfg, store = _load(args)
    reports = recover_all(_make_factory(cfg, store))
    _out(args, [r.__dict__ for r in reports])
    return EXIT_OK


def cmd_cleanup(args) -> int:
    cfg, store = _load(args)
    factory = _make_factory(cfg, store)
    job = store.job(args.id)
    actions = factory.workspaces.cleanup(Path(job["repo_path"]), args.id, merged=job["state"] == "MERGED", dry_run=args.dry_run)
    _out(args, {"job_id": args.id, "dry_run": args.dry_run, "actions": actions})
    return EXIT_OK


def cmd_dashboard(args) -> int:
    from .dashboard import serve

    cfg, store = _load(args)
    print(f"dashboard on http://{args.host}:{args.port}")
    serve(store, args.host, args.port)
    return EXIT_OK


def cmd_feature(args) -> int:
    cfg, store = _load(args)
    job = store.one("SELECT job_id FROM jobs WHERE feature_id = ? ORDER BY created_at DESC LIMIT 1", (args.feature_id,))
    if job is None:
        print(f"no job for feature {args.feature_id}", file=sys.stderr)
        return EXIT_USAGE
    job_id = job["job_id"]
    if args.feature_cmd == "classify":
        rows = store.all("SELECT * FROM complexity_assessments WHERE job_id = ? ORDER BY created_at", (job_id,))
        _out(args, [dict(r) for r in rows])
    elif args.feature_cmd == "inspect-analysis":
        rows = store.all("SELECT * FROM analysis_rounds WHERE job_id = ? ORDER BY number", (job_id,))
        open_findings = store.all("SELECT finding_id, kind, problem FROM findings WHERE job_id = ? AND status = 'open'", (job_id,))
        _out(args, {"rounds": [dict(r) for r in rows], "open_findings": [dict(f) for f in open_findings],
                    "analysis_findings": store.job_context(job_id).get("analysis_findings")})
    elif args.feature_cmd == "raise-limit":
        analysis.raise_limit(store, job_id, args.analysis_rounds, args.reason, actor=os.environ.get("USER", "operator"))
        _out(args, {"job_id": job_id, "analysis_rounds_limit": args.analysis_rounds})
    return EXIT_OK


def cmd_task(args) -> int:
    cfg, store = _load(args)
    factory = _make_factory(cfg, store)
    try:
        factory.retry_task(args.job_id, args.task_id, args.attempts, args.reason)
    except TransitionRefused as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_REFUSED
    _out(args, {"job_id": args.job_id, "task_id": args.task_id, "attempts_granted": args.attempts,
                "next_step": f"factory resume {args.job_id}"})
    return EXIT_OK


def cmd_plan(args) -> int:
    cfg, store = _load(args)
    factory = _make_factory(cfg, store)
    try:
        result = factory.approve_plan(args.id, args.budget, os.environ.get("USER", "operator"), args.reason)
    except TransitionRefused as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_REFUSED
    _out(args, {"job_id": args.id, **result, "next_step": f"factory resume {args.id}"})
    return EXIT_OK


def _ask_decisions(pending: list[dict]) -> dict[str, str]:
    """One question at a time; Enter accepts the proposal, a number picks an option, anything else is the answer."""
    answers = {}
    for d in pending:
        print(f"\n{d['id']}: {d['question']}\n  why it matters: {d['why']}")
        for i, option in enumerate(d.get("options") or [], 1):
            print(f"  {i}. {option}")
        print(f"  proposed: {d['proposed']}")
        raw = input("Answer (Enter = proposed, number = option): ").strip()
        options = d.get("options") or []
        if not raw:
            answers[d["id"]] = d["proposed"]
        elif raw.isdigit() and 1 <= int(raw) <= len(options):
            answers[d["id"]] = options[int(raw) - 1]
        else:
            answers[d["id"]] = raw
    return answers


def cmd_decide(args) -> int:
    cfg, store = _load(args)
    factory = _make_factory(cfg, store)
    answers = {}
    for item in args.answer:
        qid, sep, text = item.partition("=")
        if not sep:
            print(f"--answer expects ID=text, got {item!r}", file=sys.stderr)
            return EXIT_USAGE
        answers[qid.strip()] = text
    pending = _pending_decisions(store.job_context(args.id))
    if not answers and not args.accept_proposed:
        if args.json or not sys.stdin.isatty():
            _out(args, {"job_id": args.id, "open_decisions": pending,
                        "next_step": f"factory decide {args.id} --answer Q-1=... [--accept-proposed]"})
            return EXIT_BLOCKED if pending else EXIT_OK
        answers = _ask_decisions(pending)
    try:
        still_open = factory.answer_decisions(args.id, answers, args.accept_proposed, os.environ.get("USER", "operator"))
    except TransitionRefused as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_REFUSED
    if still_open:
        _out(args, {"job_id": args.id, "open_decisions": [d["id"] for d in still_open]})
        return EXIT_BLOCKED
    factory.resume(args.id)
    _out(args, {"job_id": args.id, "decisions": "answered", "next_step": f"factory run --job {args.id}"})
    return EXIT_OK


def cmd_budget(args) -> int:
    cfg, store = _load(args)
    budgets.raise_budget(store, args.id, args.to, args.reason, actor=os.environ.get("USER", "operator"))
    _out(args, {"job_id": args.id, "budget_limit_usd": args.to})
    return EXIT_OK


def cmd_eval(args) -> int:
    from .evals import run_suite

    cfg, store = _load(args)
    _out(args, run_suite(cfg, Path(args.suite), args.profile))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="factory", description="Agent Factory controller")
    p.add_argument("--factory-dir")
    p.add_argument("--data-dir")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("doctor").set_defaults(fn=cmd_doctor)
    s = sub.add_parser("init")
    s.add_argument("--repo", required=True)
    s.add_argument("--mode", required=True, choices=["offline", "local", "pr", "automerge", "release"])
    s.add_argument("--repo-id")
    s.add_argument("--base-branch")
    s.add_argument("--checks", help="checks manifest name in factory/checks/ (default: default)")
    s.set_defaults(fn=cmd_init)
    s = sub.add_parser("submit")
    s.add_argument("--file")
    s.add_argument("--text")
    s.add_argument("--issue", type=int)
    s.add_argument("--repo-id")
    s.add_argument("--mode", choices=["guided", "auto"],
                   help="guided: answer the product decisions after intake; auto: assume the proposals (default: config spec.default_mode)")
    s.set_defaults(fn=cmd_submit)

    s = sub.add_parser("decide", help="answer the product decisions of a guided job (then it resumes)")
    s.add_argument("id")
    s.add_argument("--answer", action="append", default=[], metavar="Q-N=TEXT")
    s.add_argument("--accept-proposed", action="store_true", help="accept the proposed answer for every decision not answered")
    s.set_defaults(fn=cmd_decide)
    s = sub.add_parser("feature")
    fsub = s.add_subparsers(dest="feature_cmd", required=True)
    for name in ("classify", "inspect-analysis"):
        f = fsub.add_parser(name)
        f.add_argument("feature_id")
    f = fsub.add_parser("raise-limit")
    f.add_argument("feature_id")
    f.add_argument("--analysis-rounds", type=int, required=True)
    f.add_argument("--reason", required=True)
    s.set_defaults(fn=cmd_feature)
    s = sub.add_parser("plan")
    psub = s.add_subparsers(dest="plan_cmd", required=True)
    pa = psub.add_parser("approve", help="approve the cost estimate of the current plan (sets the job budget)")
    pa.add_argument("id")
    pa.add_argument("--budget", type=float, help="job budget limit in USD (default: the suggested limit)")
    pa.add_argument("--reason", default="estimate accepted")
    s.set_defaults(fn=cmd_plan)
    s = sub.add_parser("task")
    tsub = s.add_subparsers(dest="task_cmd", required=True)
    t = tsub.add_parser("retry", help="give a FAILED task more attempts while the job is halted")
    t.add_argument("job_id")
    t.add_argument("task_id")
    t.add_argument("--attempts", type=int, default=1, choices=range(1, 4))
    t.add_argument("--reason", required=True)
    s.set_defaults(fn=cmd_task)
    s = sub.add_parser("run")
    s.add_argument("--job")
    s.add_argument("--queue", action="store_true")
    s.add_argument("--max-jobs", type=int, default=1)
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("status")
    s.add_argument("--job")
    s.set_defaults(fn=cmd_status)
    for name, fn in (("inspect", cmd_inspect), ("resume", cmd_resume), ("cancel", cmd_cancel), ("verify", cmd_verify)):
        s = sub.add_parser(name)
        s.add_argument("id")
        s.set_defaults(fn=fn)
    s = sub.add_parser("approve")
    s.add_argument("id")
    s.add_argument("--revision", required=True)
    s.add_argument("--scope", choices=["merge"], default="merge")
    s.set_defaults(fn=cmd_approve)
    s = sub.add_parser("reject")
    s.add_argument("id")
    s.add_argument("--revision", required=True)
    s.add_argument("--reason", required=True)
    s.set_defaults(fn=cmd_reject)
    s = sub.add_parser("rework")
    s.add_argument("id")
    s.add_argument("--file", required=True)
    s.add_argument("--revision", required=True)
    s.set_defaults(fn=cmd_rework)
    s = sub.add_parser("recover")
    s.add_argument("--all", action="store_true", required=True)
    s.set_defaults(fn=cmd_recover)
    s = sub.add_parser("cleanup")
    s.add_argument("id")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(fn=cmd_cleanup)
    s = sub.add_parser("dashboard")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(fn=cmd_dashboard)
    s = sub.add_parser("budget")
    bsub = s.add_subparsers(dest="budget_cmd", required=True)
    b = bsub.add_parser("raise")
    b.add_argument("id")
    b.add_argument("--to", type=float, required=True)
    b.add_argument("--reason", required=True)
    s.set_defaults(fn=cmd_budget)
    s = sub.add_parser("eval")
    s.add_argument("--suite", required=True)
    s.add_argument("--profile", required=True, choices=["full", "single-agent"])
    s.set_defaults(fn=cmd_eval)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return EXIT_USAGE
    except SandboxUnavailable as exc:
        print(f"sandbox unavailable, refusing to run code: {exc}", file=sys.stderr)
        return EXIT_BLOCKED
    except TransitionRefused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return EXIT_REFUSED


if __name__ == "__main__":
    raise SystemExit(main())
