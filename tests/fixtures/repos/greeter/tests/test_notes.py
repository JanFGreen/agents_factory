from app.notes import NoteStore


def test_add_and_list(tmp_path):
    store = NoteStore(tmp_path / "n.json")
    store.add("a", "x")
    assert [n["text"] for n in store.list_for("a")] == ["x"]
