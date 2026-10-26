"""Tiny persistence layer used by acceptance scenarios."""
import json
from pathlib import Path


class NoteStore:
    def __init__(self, path: Path):
        self.path = path

    def _load(self) -> list[dict]:
        return json.loads(self.path.read_text()) if self.path.exists() else []

    def add(self, owner: str, text: str) -> dict:
        notes = self._load()
        note = {"id": len(notes) + 1, "owner": owner, "text": text}
        notes.append(note)
        self.path.write_text(json.dumps(notes))
        return note

    def list_for(self, owner: str) -> list[dict]:
        return [n for n in self._load() if n["owner"] == owner]
