"""StockLists on-hand variance analysis for movement imports."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy.orm import Session

from stock_analysis.analytics.movement_projection import project_post_movement_on_hand
from stock_analysis.importers.movement_parser import MovementRow
from stock_analysis.importers.item_filters import should_skip_item
from stock_analysis.importers.stocklist_parser import StocklistParseResult


@dataclass
class StocklistVarianceLine:
    sku: str
    name: str
    projected_qty: float
    stocklist_qty: float
    variance: float
    line_type: str  # match, shortage, overage, new_in_stocklist, missing_from_stocklist


@dataclass
class StocklistComparison:
    file_name: str = ""
    lines: list[StocklistVarianceLine] = field(default_factory=list)

    @property
    def variance_lines(self) -> list[StocklistVarianceLine]:
        return [line for line in self.lines if abs(line.variance) > 0.0001]

    @property
    def exact_matches(self) -> int:
        return sum(1 for line in self.lines if abs(line.variance) <= 0.0001)


def compare_movement_to_stocklist(
    session: Session,
    movement_rows: list[MovementRow],
    stocklist: StocklistParseResult,
    *,
    direction: Literal["forward", "backward"] = "forward",
) -> StocklistComparison:
    stocklist_by_sku = {
        row.code: row
        for row in stocklist.rows
        if not should_skip_item(row.code, row.description)
    }
    projected = project_post_movement_on_hand(session, movement_rows, direction=direction)

    lines: list[StocklistVarianceLine] = []
    seen: set[str] = set()

    for sku, sl_row in stocklist_by_sku.items():
        seen.add(sku)
        projected_qty, projected_name = projected.get(sku, (0.0, sl_row.description))
        stocklist_qty = sl_row.on_hand
        variance = stocklist_qty - projected_qty
        line_type = "match"
        if sku not in projected:
            line_type = "new_in_stocklist"
        elif variance > 0.0001:
            line_type = "overage"
        elif variance < -0.0001:
            line_type = "shortage"
        lines.append(
            StocklistVarianceLine(
                sku=sku,
                name=sl_row.description or projected_name,
                projected_qty=projected_qty,
                stocklist_qty=stocklist_qty,
                variance=variance,
                line_type=line_type,
            )
        )

    for sku, (projected_qty, name) in projected.items():
        if sku in seen:
            continue
        lines.append(
            StocklistVarianceLine(
                sku=sku,
                name=name,
                projected_qty=projected_qty,
                stocklist_qty=0.0,
                variance=-projected_qty,
                line_type="missing_from_stocklist",
            )
        )

    lines.sort(key=lambda line: abs(line.variance), reverse=True)
    return StocklistComparison(lines=lines)
