from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]


def test_topbar_has_mas_logo():
    content = (BASE_DIR / "templates/partials/topbar.html").read_text(encoding="utf-8")
    assert "assets/branding/mas-logo.png" in content


def test_login_has_mas_logo():
    content = (BASE_DIR / "templates/auth/login.html").read_text(encoding="utf-8")
    assert "assets/branding/mas-logo.png" in content


def test_base_mentions_mas_group_and_current_year():
    content = (BASE_DIR / "templates/base.html").read_text(encoding="utf-8")
    assert "MAS Group" in content
    assert "current_year" in content
