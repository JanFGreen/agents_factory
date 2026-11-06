"""Recurring defects: the mistakes builders keep making in one repository, and the check that should catch each.

A review catches the same class of defect job after job (LearnEnglish F1-F6: low contrast in 11 jobs, small touch
targets in 12, layout shifts in 8), and every catch costs a task attempt. A class that recurs belongs in the trusted
gate, where it fails before any review: `factory lessons` lists the classes and writes a feature request that adds the
check. The checks manifest's `lessons` table pairs each class with the check that enforces it; a class that still
turns up after its `since` date is a repeat, and the check does not catch it. Until a class is enforced the builder
gets it as `recurring_defects`.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from datetime import date

from .state import Store

# Matchers over finding text: the controller's own measurement findings and the reviewers' wording of the same defect.
CLASSES: dict[str, str] = {
    "contrast": r"\bcontrast\b[^.]*\d(?:\.\d+)?:1",
    "target-size": r"\b(?:touch|tap|hit|click)[ -]?(?:target|area)\b|\b\d{1,2}\s*[x×]\s*\d{1,2}\s*px\b",
    "layout-shift": r"\bmoves \d+px\b|\blayout shift",
    "focus-ring": r"\bfocus ?ring\b|\bdefault (?:focus )?outline\b|\bno (?:explicit|tokenised|visible) focus\b",
    "first-screen": r"\bfirst screen does not show\b",
}


def finding_class(problem: str, classes: dict[str, str]) -> str | None:
    return next((name for name, rx in classes.items() if re.search(rx, problem, re.IGNORECASE)), None)


def recurring(store: Store, repo_path: str, *, classes: dict[str, str], enforced: dict, min_jobs: int,
              examples: int = 3) -> list[dict]:
    """Classes found in at least ``min_jobs`` jobs of the repo, and enforced classes found again after their date."""
    rows = store.all("SELECT f.finding_id, f.job_id, f.kind, f.blocking, f.problem, f.location_json, f.created_at"
                     " FROM findings f JOIN jobs j ON j.job_id = f.job_id WHERE j.repo_path = ? ORDER BY f.created_at",
                     (repo_path,))
    grouped: dict[str, list] = defaultdict(list)
    for row in rows:
        if name := finding_class(row["problem"], classes):
            grouped[name].append(row)
    out = []
    for name, found in grouped.items():
        rule = enforced.get(name)
        since = rule.since if rule else None
        after = [r for r in found if since and r["created_at"][:10] > since]
        jobs = {r["job_id"] for r in found}
        if (len(jobs) < min_jobs and not rule) or (rule and not after):
            continue
        # the newest finding of each job first (blocking before the rest), so the examples show the spread
        ranked = sorted(reversed(after or found), key=lambda r: not r["blocking"])
        sample = list({r["job_id"]: r for r in reversed(ranked)}.values())[::-1][:examples]
        out.append({
            "class": name, "findings": len(found), "blocking": sum(1 for r in found if r["blocking"]), "jobs": len(jobs),
            "enforced_by": rule.check if rule else None, "since": since, "found_after_enforcement": len(after),
            "examples": [{"job_id": r["job_id"], "finding_id": r["finding_id"], "review": r["kind"], "problem": r["problem"][:300],
                          "location": json.loads(r["location_json"]) if r["location_json"] else None} for r in sample],
        })
    return sorted(out, key=lambda c: (-c["jobs"], -c["findings"]))


def proposal(repo_id: str, lesson: dict) -> dict:
    """A feature request that moves one class into the trusted gate, proven on a past finding."""
    quoted = "\n".join(f"- {e['problem']} ({e['review']}, {e['job_id']})" for e in lesson["examples"])
    repeat = (f" The check {lesson['enforced_by']} has enforced it since {lesson['since']} and still let "
              f"{lesson['found_after_enforcement']} through, so change or replace it." if lesson["enforced_by"] else "")
    return {
        "feature_id": f"LESSON-{lesson['class']}-{date.today().isoformat()}",
        "repo_id": repo_id,
        "goal": f"The trusted checks fail on {lesson['class']} defects before review",
        "original_request": (
            f"Reviews found {lesson['class']} defects {lesson['findings']} times in {lesson['jobs']} jobs of this repository"
            f" ({lesson['blocking']} of them blocking), each found only after a task was built:{repeat}\n{quoted}\n\n"
            "Add a check that runs in the existing test command and fails on this class of defect, so a builder sees it "
            "before review. Prefer making the defect impossible (a shared component or token that is right by "
            "construction) over a test that detects it, and a test over a written rule. The check reads the real source "
            "(theme tokens, components, styles), not a copy. Prove it on a past defect: a fixture that reproduces one of "
            "the findings above fails the check, and the current code passes it, or the code is fixed in this change."),
        "constraints": ["Only this class of defect; no other refactoring",
                        "Every acceptance criterion is proven by one run of the existing checks"],
    }
