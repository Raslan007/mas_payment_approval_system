from pathlib import Path


def test_suppliers_list_datatable_search_hook():
    template = Path("templates/suppliers/list.html").read_text(encoding="utf-8")

    assert 'id="suppliersTable"' in template
    assert 'id="suppliersSearch"' in template
    assert "#suppliersTable" in template
