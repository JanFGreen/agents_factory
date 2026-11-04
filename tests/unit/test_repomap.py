from __future__ import annotations

from agent_factory.repomap import api_map

TS = """import { x } from './x';

export interface Progress {
  words: Record<string, Word>;
  resetAt: number;
}

export function mergeProgress(
  a: Progress,
  b: Progress,
): Progress {
  return a;
}

function hidden() {}
export const LIMIT = 500;
export type Grade = 'again' | 'good';
"""

PY = '''class Store:
    def get(self, key: str) -> str:
        return key

    def _private(self):
        pass


def public(a: int) -> int:
    return a


def _helper():
    pass
'''


def test_the_map_shows_exports_with_type_bodies_and_skips_tests(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "progress.ts").write_text(TS)
    (tmp_path / "src" / "progress.test.ts").write_text("export function t() {}\n")
    (tmp_path / "app.py").write_text(PY)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_x():\n    pass\n")
    found = api_map(tmp_path, ["src/progress.ts", "src/progress.test.ts", "app.py", "tests/test_app.py", "README.md"])
    assert set(found["files"]) == {"src/progress.ts", "app.py"}
    ts = found["files"]["src/progress.ts"]
    assert "export interface Progress {\n  words: Record<string, Word>;\n  resetAt: number;\n}" in ts
    assert "export function mergeProgress(\n  a: Progress,\n  b: Progress,\n): Progress {" in ts  # joined until ) closes
    assert "export const LIMIT = 500;" in ts and "export type Grade" in ts
    assert "hidden" not in ts and "return a" not in ts
    py = found["files"]["app.py"]
    assert py.splitlines() == ["class Store:", "    def get(self, key: str) -> str:", "def public(a: int) -> int:"]


def test_files_past_the_size_cap_are_named_not_cut(tmp_path):
    for name in ("a.py", "b.py"):
        (tmp_path / name).write_text("def f(x):\n    return x\n" * 1)
    found = api_map(tmp_path, ["a.py", "b.py"], max_chars=20)
    assert list(found["files"]) == ["a.py"] and found["omitted"] == ["b.py"]
