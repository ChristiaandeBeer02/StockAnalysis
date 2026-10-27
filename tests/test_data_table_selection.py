"""Tests for DataTable multi-select export helpers."""

from __future__ import annotations

import os
from datetime import datetime

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QItemSelectionModel
from PySide6.QtWidgets import QApplication

from stock_analysis.ui.export_dialog import stamped_export_name
from stock_analysis.ui.widgets.data_table import DataTable


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def test_stamped_export_name():
    stamp = datetime(2026, 10, 27, 10, 19)
    assert stamped_export_name("sales", "xlsx", stamp) == "sales_2026-10-27_1019.xlsx"
    assert stamped_export_name("dead_stock", "pdf", stamp) == "dead_stock_2026-10-27_1019.pdf"


def test_selected_rows_as_lists(qapp):
    table = DataTable()
    table.set_headers(["SKU", "Name"])
    table.set_rows([["A", "Alpha"], ["B", "Beta"], ["C", "Gamma"]])
    model = table.model()
    selection = table.selectionModel()
    flags = QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows
    selection.select(model.index(0, 0), flags)
    selection.select(model.index(2, 0), flags)

    assert table.headers() == ["SKU", "Name"]
    assert table.selected_row_indices() == [0, 2]
    assert table.rows_as_lists(table.selected_row_indices()) == [
        ["A", "Alpha"],
        ["C", "Gamma"],
    ]
    assert table.rows_as_lists() == [
        ["A", "Alpha"],
        ["B", "Beta"],
        ["C", "Gamma"],
    ]

    table.clear_selection()
    assert table.selected_row_indices() == []
