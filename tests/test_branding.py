from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read_text(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_branding_tokens_exist():
    css = read_text("static/css/styles.css")
    tokens = [
        "--brand-primary",
        "--brand-accent",
        "--brand-bg",
        "--brand-muted",
        "--brand-radius",
        "--brand-shadow",
        "--brand-transition",
    ]
    for token in tokens:
        assert token in css


def test_logo_references_exist():
    template_paths = [
        "templates/base.html",
        "templates/auth/login.html",
        "templates/partials/topbar.html",
    ]

    for template_path in template_paths:
        template = read_text(template_path)
        assert "mas-logo.png" in template
        assert "mas-logo-dark.png" in template
