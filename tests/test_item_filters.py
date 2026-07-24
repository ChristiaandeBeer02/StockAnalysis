"""Tests for item junk filters."""

from stock_analysis.importers.item_filters import item_status, should_skip_item


def test_junk_skus():
    assert should_skip_item(".", "Open Item For Quotation")
    assert should_skip_item("* 15582", "zzzzzz")
    assert should_skip_item("*** END OF REPORT ***", "")
    assert should_skip_item("Totals:", "339390.22")
    assert not should_skip_item("03MAD0067", "Screw Selftapper")


def test_item_status():
    assert item_status(is_deprecated=True, not_in_turn_report=False, has_enrichment=True) == "Deprecated"
    assert (
        item_status(is_deprecated=False, not_in_turn_report=True, has_enrichment=True) == "No turn data"
    )
    assert item_status(is_deprecated=False, not_in_turn_report=True, has_enrichment=False) == "Active"
    assert item_status(is_deprecated=False, not_in_turn_report=False, has_enrichment=True) == "Active"
