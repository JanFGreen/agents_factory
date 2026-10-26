from app.greet import greet


def test_greet_contains_name():
    assert "Ada" in greet("Ada")
