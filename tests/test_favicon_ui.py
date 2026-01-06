from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]


def test_base_template_includes_favicons():
    content = (BASE_DIR / "templates/base.html").read_text(encoding="utf-8")
    assert "favicon.ico" in content
    assert "favicon-32x32.png" in content
    assert "favicon-16x16.png" in content
