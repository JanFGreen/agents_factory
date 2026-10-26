"""AF-01: every manifest file (including dotfiles) exists and parses; skills have valid frontmatter."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys

import yaml

from ..conftest import ROOT

MANIFEST = json.loads((ROOT / "factory" / "manifests" / "install-files.json").read_text())


def test_af01_manifest_files_exist_and_parse(tmp_path):
    files = MANIFEST["files"]
    assert ".gitignore" in files and any(f.startswith("factory/skills/") for f in files)
    target = tmp_path / "fresh"
    for rel in files:
        src = ROOT / rel
        assert src.is_file(), f"manifest lists a missing file: {rel}"
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    for rel in files:
        path = target / rel
        if rel.endswith((".yaml", ".yml")):
            yaml.safe_load(path.read_text())
        elif rel.endswith(".json"):
            json.loads(path.read_text())


def test_every_skill_has_contract_frontmatter_and_examples():
    skills = sorted(p for p in (ROOT / "factory" / "skills").iterdir() if p.is_dir() and not p.name.startswith("_"))
    assert len(skills) == 22
    from agent_factory.schemas import AgentEnvelope
    for skill in skills:
        text = (skill / "SKILL.md").read_text()
        front = yaml.safe_load(text.split("---")[1])
        assert front["name"] == skill.name and front["description"]
        for example in ("pass", "fail", "blocked"):
            AgentEnvelope.model_validate(json.loads((skill / "examples" / f"{example}.json").read_text()))


def test_fresh_install_config_loads_and_doctor_runs(tmp_path):
    target = tmp_path / "fresh"
    for rel in MANIFEST["files"]:
        (target / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target / rel)
    proc = subprocess.run([sys.executable, "-m", "agent_factory.cli", "--factory-dir", str(target / "factory"),
                           "--data-dir", str(tmp_path / "data"), "--json", "doctor"], capture_output=True, text=True, cwd=target)
    report = json.loads(proc.stdout)
    assert report["config"]["ok"] and report["sandbox"]["ok"] and report["skills"]["count"] == 22


def test_openspec_schema_validates():
    binary = ROOT / "tools" / "openspec" / "node_modules" / ".bin" / "openspec"
    if not binary.exists():
        import pytest
        pytest.skip("openspec not installed (npm ci --prefix tools/openspec)")
    proc = subprocess.run([str(binary), "schema", "validate", "factory-autonomous"], cwd=ROOT, capture_output=True, text=True,
                          env={"PATH": "/usr/bin:/bin:" + str(binary.parent) + ":" + shutil.which("node").rsplit("/", 1)[0], "OPENSPEC_TELEMETRY": "0", "HOME": str(ROOT)})
    assert proc.returncode == 0 and "valid" in (proc.stdout + proc.stderr)
