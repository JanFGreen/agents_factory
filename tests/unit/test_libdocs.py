import json

from agent_factory.libdocs import docs_for


def test_the_builder_gets_docs_of_the_installed_libraries_its_files_import(tmp_path):
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": {"zod": "^3.23.8", "react": "19.0.0"},
                                                       "devDependencies": {"vitest": "2.1.0"}}))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "form.ts").write_text("import { z } from 'zod';\nimport './local';\nimport fs from 'node:fs';\n")
    urls = []

    def fetch(url):
        urls.append(url)
        if "/search?" in url:
            return json.dumps({"results": [{"id": "/colinhacks/zod"}]})
        return "z.object({...}).safeParse(input)"

    cache = tmp_path / "cache"
    first = docs_for(tmp_path, ["src/form.ts"], cache, fetch=fetch)
    assert first == [{"library": "zod", "installed_version": "3.23.8", "docs": "z.object({...}).safeParse(input)"}]
    assert urls[0].endswith("/search?query=zod") and urls[1].startswith("https://context7.com/api/v1/colinhacks/zod?")
    assert docs_for(tmp_path, ["src/form.ts"], cache, fetch=fetch) == first and len(urls) == 2  # cached per version


def test_an_unknown_library_or_a_failing_service_is_skipped(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[project]\ndependencies = [\n  "httpx>=0.27",\n  "pydantic==2.9.0",\n]\n')
    (tmp_path / "app.py").write_text("import httpx\nfrom pydantic import BaseModel\n")

    def fetch(url):
        if "httpx" in url:
            return json.dumps({"results": []})
        raise OSError("offline")

    assert docs_for(tmp_path, ["app.py"], tmp_path / "cache", fetch=fetch) == []


def test_with_context7_on_a_build_still_runs_when_the_service_is_down(make_env):
    env = make_env("trivial", docs={"context7": True})
    env.factory.docs_fetch = lambda url: (_ for _ in ()).throw(OSError("offline"))
    assert env.factory.run(env.submit()) == "READY_TO_MERGE"
