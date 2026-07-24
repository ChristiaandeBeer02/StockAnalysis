"""Stock take variance analysis."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from stock_analysis.db.models import BaselineItem, Item
from stock_analysis.importers.iq_retail_parser import ParseStats
from stock_analysis.importers.item_filters import should_skip_item
from stock_analysis.importers.stockholding_parser import StockholdingParseResult, StockholdingRow


@dataclass
class VarianceLine:
    sku: str
    name: str
    baseline_qty: float
    counted_qty: float
    variance: float
    unit_cost: float | None
    variance_value: float
    line_type: str  # match, shortage, overage, new_in_stock_take, missing_from_stock_take


@dataclass
class StockTakeComparison:
    file_name: str
    period_start: str | None
    period_end: str | None
    lines: list[VarianceLine] = field(default_factory=list)
    parse_stats: ParseStats | None = None

    @property
    def variance_lines(self) -> list[VarianceLine]:
        return [line for line in self.lines if abs(line.variance) > 0.0001]

    @property
    def exact_matches(self) -> int:
        return sum(1 for line in self.lines if abs(line.variance) <= 0.0001)

    @property
    def shrinkage_value(self) -> float:
        return sum(line.variance_value for line in self.lines if line.variance_value < 0)

    @property
    def overage_value(self) -> float:
        return sum(line.variance_value for line in self.lines if line.variance_value > 0)

    def summary_dict(self) -> dict:
        return {
            "file_name": self.file_name,
            "period_start": self.period_start,
            "period_end": self.period_end,
            "items_compared": len(self.lines),
            "exact_matches": self.exact_matches,
            "variance_count": len(self.variance_lines),
            "shrinkage_value": self.shrinkage_value,
            "overage_value": self.overage_value,
        }


def compare_stock_take(session: Session, parsed: StockholdingParseResult) -> StockTakeComparison:
    stock_take_by_sku: dict[str, StockholdingRow] = {}
    for row in parsed.rows:
        if should_skip_item(row.code, row.description) or row.is_deprecated:
            continue
        stock_take_by_sku[row.code] = row

    baseline_rows = session.execute(
        select(Item, BaselineItem)
        .join(BaselineItem, BaselineItem.item_id == Item.id)
        .where(Item.is_deprecated.is_(False))
    ).all()

    baseline_by_sku: dict[str, tuple[Item, BaselineItem]] = {}
    for item, baseline in baseline_rows:
        if should_skip_item(item.sku, item.name):
            continue
        baseline_by_sku[item.sku] = (item, baseline)

    lines: list[VarianceLine] = []
    seen: set[str] = set()

    for sku, row in stock_take_by_sku.items():
        seen.add(sku)
        counted = row.on_hand
        unit_cost = row.unit_cost
        if sku in baseline_by_sku:
            item, baseline = baseline_by_sku[sku]
            base_qty = baseline.qty_on_hand
            cost = unit_cost or item.unit_cost
            variance = counted - base_qty
            line_type = "match"
            if variance > 0.0001:
                line_type = "overage"
            elif variance < -0.0001:
                line_type = "shortage"
            lines.append(
                VarianceLine(
                    sku=sku,
                    name=row.description or item.name,
                    baseline_qty=base_qty,
                    counted_qty=counted,
                    variance=variance,
                    unit_cost=cost,
                    variance_value=(variance * cost) if cost else 0.0,
                    line_type=line_type,
                )
            )
        else:
            lines.append(
                VarianceLine(
                    sku=sku,
                    name=row.description,
                    baseline_qty=0.0,
                    counted_qty=counted,
                    variance=counted,
                    unit_cost=unit_cost,
                    variance_value=(counted * unit_cost) if unit_cost else 0.0,
                    line_type="new_in_stock_take",
                )
            )

    for sku, (item, baseline) in baseline_by_sku.items():
        if sku in seen:
            continue
        cost = item.unit_cost
        base_qty = baseline.qty_on_hand
        lines.append(
            VarianceLine(
                sku=sku,
                name=item.name,
                baseline_qty=base_qty,
                counted_qty=0.0,
                variance=-base_qty,
                unit_cost=cost,
                variance_value=(-base_qty * cost) if cost else 0.0,
                line_type="missing_from_stock_take",
            )
        )

    lines.sort(key=lambda x: x.variance_value)
    return StockTakeComparison(
        file_name="",
        period_start=parsed.period_start,
        period_end=parsed.period_end,
        lines=lines,
    )
