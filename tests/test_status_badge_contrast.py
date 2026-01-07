from pathlib import Path


def test_status_badge_contrast_styles_present():
    css_path = Path(__file__).resolve().parents[1] / "static" / "css" / "styles.css"
    content = css_path.read_text(encoding="utf-8")

    assert "background:#DBEAFE" in content
    assert "color:#1E3A8A" in content
    assert "opacity: 1" in content
    assert "border: 1px solid rgba(0,0,0,0.05)" in content
