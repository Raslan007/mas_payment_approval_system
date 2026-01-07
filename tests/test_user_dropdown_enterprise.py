from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]


def test_topbar_has_enterprise_dropdown_markup():
    content = (BASE_DIR / "templates/partials/topbar.html").read_text(encoding="utf-8")
    assert "dropdown-menu" in content
    assert "dropdown-toggle" in content


def test_topbar_has_enterprise_dropdown_labels():
    content = (BASE_DIR / "templates/partials/topbar.html").read_text(encoding="utf-8")
    assert "الملف الشخصي" in content
    assert "إعدادات الحساب" in content
    assert "تغيير كلمة المرور" in content
    assert "المساعدة والدعم" in content
    assert "تسجيل الخروج" in content


def test_user_menu_styles_exist():
    content = (BASE_DIR / "static/css/styles.css").read_text(encoding="utf-8")
    assert ".user-menu-dropdown" in content
    assert ".user-menu-item--disabled" in content
