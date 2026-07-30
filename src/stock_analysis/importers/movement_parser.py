"""Parser for IQ Retail Sales_Detail and PurchasesDetailed movement exports."""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path

from stock_analysis.importers.iq_retail_parser import is_deprecated_description, parse_float
from stock_analysis.importers.item_filters import should_skip_item


@dataclass
class MovementRow:
    code: str
    description: str
    department: str
    avg_cost: float
    on_hand_snapshot: float
    sales_qty: float
    refunds_qty: float
    net_sales_qty: float
    purchases_qty: float
    returns_qty: float
    net_purchases_qty: float
    net_sales_revenue: float = 0.0
    net_sales_cost: float = 0.0
    gross_profit: float = 0.0
    is_deprecated: bool = False


@dataclass
class MovementParseResult:
    rows: list[MovementRow]
    sales_mismatch_skus: list[str] = field(default_factory=list)


def _read_csv_dicts(path: Path) -> list[dict[str, str]]:
    for encoding in ("utf-8-sig", "latin-1", "cp1252"):
        try:
            with path.open(newline="", encoding=encoding) as handle:
                return list(csv.DictReader(handle))
        except UnicodeDecodeError:
            continue
    with path.open(newline="", encoding="latin-1", errors="replace") as handle:
        return list(csv.DictReader(handle))


def _field(row: dict[str, str], *names: str) -> str:
    for name in names:
        if name in row and row[name] is not None:
            return str(row[name]).strip()
    return ""


def _float_field(row: dict[str, str], *names: str) -> float:
    return parse_float(_field(row, *names))


def _parse_sales_monetary(raw: dict[str, str]) -> tuple[float, float, float]:
    net_sales_revenue = _float_field(raw, "NettSales")
    if net_sales_revenue == 0.0:
        gross_sales = _float_field(raw, "Sales")
        if gross_sales != 0.0:
            net_sales_revenue = gross_sales

    net_sales_cost = _float_field(raw, "NettCost")
    if net_sales_cost == 0.0:
        sales_cost = _float_field(raw, "SalesCost")
        if sales_cost != 0.0:
            net_sales_cost = sales_cost

    profit = _float_field(raw, "Profit")
    if profit == 0.0 and (net_sales_revenue != 0.0 or net_sales_cost != 0.0):
        profit = net_sales_revenue - net_sales_cost

    return net_sales_revenue, net_sales_cost, profit


def _movement_subdepartment(raw: dict[str, str]) -> str:
    return _field(raw, "SUBDEPARTM", "Subdepartm")


def parse_sales_detail_file(path: Path) -> dict[str, MovementRow]:
    rows: dict[str, MovementRow] = {}
    for raw in _read_csv_dicts(path):
        code = _field(raw, "CODE", "Code")
        description = _field(raw, "Descript", "Description")
        if not code or should_skip_item(code, description):
            continue

        sales_qty = _float_field(raw, "SalesQty", "Units")
        refunds_qty = _float_field(raw, "RefundsQty")
        net_sales_qty = _float_field(raw, "NettSalesQuantity")
        if net_sales_qty == 0.0 and (sales_qty != 0.0 or refunds_qty != 0.0):
            net_sales_qty = sales_qty - refunds_qty

        revenue, cost, profit = _parse_sales_monetary(raw)

        rows[code] = MovementRow(
            code=code,
            description=description,
            department=_movement_subdepartment(raw),
            avg_cost=_float_field(raw, "AvrgCost"),
            on_hand_snapshot=_float_field(raw, "OnHand"),
            sales_qty=sales_qty,
            refunds_qty=refunds_qty,
            net_sales_qty=net_sales_qty,
            purchases_qty=0.0,
            returns_qty=0.0,
            net_purchases_qty=0.0,
            net_sales_revenue=revenue,
            net_sales_cost=cost,
            gross_profit=profit,
            is_deprecated=is_deprecated_description(description),
        )
    return rows


def parse_purchases_detailed_file(path: Path) -> dict[str, dict[str, float]]:
    purchases: dict[str, dict[str, float]] = {}
    for raw in _read_csv_dicts(path):
        code = _field(raw, "Code", "CODE")
        if not code:
            continue
        purchases[code] = {
            "sales_qty": _float_field(raw, "Units"),
            "refunds_qty": _float_field(raw, "RefundsQty"),
            "purchases_qty": _float_field(raw, "PurchasesQT"),
            "returns_qty": _float_field(raw, "RETURNSQT"),
        }
    return purchases


def merge_movement_reports(sales_path: Path, purchases_path: Path) -> MovementParseResult:
    sales_rows = parse_sales_detail_file(sales_path)
    purchase_rows = parse_purchases_detailed_file(purchases_path)

    mismatch_skus: list[str] = []
    merged: dict[str, MovementRow] = dict(sales_rows)

    for code, purchase in purchase_rows.items():
        if code in merged:
            row = merged[code]
            purch_sales = purchase["sales_qty"]
            purch_refunds = purchase["refunds_qty"]
            if abs(row.sales_qty - purch_sales) > 0.001 or abs(row.refunds_qty - purch_refunds) > 0.001:
                mismatch_skus.append(code)
            row.purchases_qty = purchase["purchases_qty"]
            row.returns_qty = purchase["returns_qty"]
            row.net_purchases_qty = purchase["purchases_qty"] - purchase["returns_qty"]
        else:
            sales_qty = purchase["sales_qty"]
            refunds_qty = purchase["refunds_qty"]
            merged[code] = MovementRow(
                code=code,
                description="",
                department="",
                avg_cost=0.0,
                on_hand_snapshot=0.0,
                sales_qty=sales_qty,
                refunds_qty=refunds_qty,
                net_sales_qty=sales_qty - refunds_qty,
                purchases_qty=purchase["purchases_qty"],
                returns_qty=purchase["returns_qty"],
                net_purchases_qty=purchase["purchases_qty"] - purchase["returns_qty"],
            )

    active_rows = [
        row
        for row in merged.values()
        if not should_skip_item(row.code, row.description)
        and (
            row.net_sales_qty != 0.0
            or row.net_purchases_qty != 0.0
            or row.sales_qty != 0.0
            or row.purchases_qty != 0.0
        )
    ]
    active_rows.sort(key=lambda row: row.code)

    return MovementParseResult(rows=active_rows, sales_mismatch_skus=mismatch_skus)

