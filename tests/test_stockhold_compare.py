"""Tests for stockholding file-vs-file comparison."""

from stock_analysis.analytics.stockhold_compare import (
    UNKNOWN_DEPARTMENT,
    compare_stockholdings,
    filter_diff_lines,
    format_value_diff,
    has_difference,
)
from stock_analysis.importers.iq_retail_parser import ParseStats
from stock_analysis.importers.stockholding_parser import StockholdingParseResult, StockholdingRow


def _make_parsed(rows: list[tuple[str, str, float, float, bool]]) -> StockholdingParseResult:
    parsed_rows = [
        StockholdingRow(
            code=code,
            description=name,
            on_hand=qty,
            stock_value=value,
            unit_cost=(value / qty) if qty else None,
            is_deprecated=is_deprecated,
        )
        for code, name, qty, value, is_deprecated in rows
    ]
    return StockholdingParseResult(
        rows=parsed_rows,
        period_start="01/01/2026",
        period_end="31/01/2026",
        date_printed=None,
        stats=ParseStats(total_rows=len(parsed_rows), deprecated_rows=0, skipped_rows=0),
    )


def test_compare_exact_match():
    rows = [("SKU001", "Widget", 10.0, 100.0, False)]
    first = _make_parsed(rows)
    second = _make_parsed(rows)

    lines = compare_stockholdings(first, second)
    assert len(lines) == 1
    assert lines[0].on_hand_diff == 0.0
    assert lines[0].value_diff == 0.0
    assert not has_difference(lines[0])


def test_compare_shortage_and_overage():
    first = _make_parsed(
        [
            ("SKU001", "Widget A", 10.0, 100.0, False),
            ("SKU002", "Widget B", 5.0, 50.0, False),
        ]
    )
    second = _make_parsed(
        [
            ("SKU001", "Widget A", 6.0, 60.0, False),
            ("SKU002", "Widget B", 8.0, 80.0, False),
        ]
    )

    lines = compare_stockholdings(first, second)
    by_sku = {line.sku: line for line in lines}
    assert by_sku["SKU001"].on_hand_diff == -4.0
    assert by_sku["SKU001"].value_diff == -40.0
    assert by_sku["SKU002"].on_hand_diff == 3.0
    assert by_sku["SKU002"].value_diff == 30.0


def test_compare_only_in_first_file():
    first = _make_parsed([("SKU001", "Widget", 4.0, 40.0, False)])
    second = _make_parsed([])

    lines = compare_stockholdings(first, second)
    assert len(lines) == 1
    assert lines[0].on_hand_diff == -4.0
    assert lines[0].value_diff == -40.0


def test_compare_only_in_second_file():
    first = _make_parsed([])
    second = _make_parsed([("SKU002", "Gadget", 2.0, 20.0, False)])

    lines = compare_stockholdings(first, second)
    assert len(lines) == 1
    assert lines[0].on_hand_diff == 2.0
    assert lines[0].value_diff == 20.0
    assert lines[0].name == "Gadget"


def test_compare_excludes_junk_and_deprecated():
    first = _make_parsed(
        [
            (".", "Open Item For Quotation", 1.0, 10.0, False),
            ("SKU001", "zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz", 1.0, 10.0, True),
            ("SKU002", "Active", 1.0, 10.0, False),
        ]
    )
    second = _make_parsed([("SKU002", "Active", 2.0, 20.0, False)])

    lines = compare_stockholdings(first, second)
    assert [line.sku for line in lines] == ["SKU002"]


def test_filter_differences_only():
    lines = compare_stockholdings(
        _make_parsed([("SKU001", "A", 1.0, 10.0, False), ("SKU002", "B", 5.0, 50.0, False)]),
        _make_parsed([("SKU001", "A", 1.0, 10.0, False), ("SKU002", "B", 8.0, 80.0, False)]),
    )

    filtered = filter_diff_lines(lines, differences_only=True)
    assert len(filtered) == 1
    assert filtered[0].sku == "SKU002"


def test_filter_department_including_unknown():
    first = _make_parsed(
        [
            ("SKU001", "A", 1.0, 10.0, False),
            ("SKU002", "B", 1.0, 10.0, False),
        ]
    )
    second = _make_parsed(
        [
            ("SKU001", "A", 2.0, 20.0, False),
            ("SKU002", "B", 2.0, 20.0, False),
        ]
    )
    dept_by_sku = {"SKU001": "D01"}

    lines = compare_stockholdings(first, second, dept_by_sku=dept_by_sku)
    assert lines[0].department == "D01"
    assert lines[1].department == UNKNOWN_DEPARTMENT

    dept_filtered = filter_diff_lines(lines, dept_filter="D01")
    assert [line.sku for line in dept_filtered] == ["SKU001"]

    unknown_filtered = filter_diff_lines(lines, dept_filter=UNKNOWN_DEPARTMENT)
    assert [line.sku for line in unknown_filtered] == ["SKU002"]


def test_format_value_diff():
    assert format_value_diff(0.0) == "—"
    assert format_value_diff(12.5) == "R +12.50"
    assert format_value_diff(-3.0) == "R -3.00"
