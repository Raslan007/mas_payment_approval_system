from pathlib import Path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_manifest_has_expected_fields():
    manifest_path = Path("static/assets/branding/manifest.webmanifest")
    content = read_text(manifest_path)
    assert "MAS EMS" in content
    assert "standalone" in content
    assert "theme_color" in content


def test_base_has_pwa_meta_tags():
    base_path = Path("templates/base.html")
    content = read_text(base_path)
    assert "manifest.webmanifest" in content
    assert "theme-color" in content
    assert "apple-touch-icon" in content


def test_logo_dark_mode_attributes():
    topbar_path = Path("templates/partials/topbar.html")
    login_path = Path("templates/auth/login.html")
    topbar_content = read_text(topbar_path)
    login_content = read_text(login_path)

    assert "mas-logo.png" in topbar_content
    assert "mas-logo-dark.png" in topbar_content
    assert "mas-logo.png" in login_content
    assert "mas-logo-dark.png" in login_content
