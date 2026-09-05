"""Placeholder so the CLI suite runs before real tests exist."""


def test_app_imports():
    from groundline_cli.main import app

    assert app is not None
