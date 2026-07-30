"""In-memory cache for expensive dashboard summaries."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from stock_analysis.analytics.dashboard import build_period_summary, list_period_batches
from stock_analysis.analytics.lookback import DEFAULT_LOOKBACK_WEEKS, get_lookback_weeks
from stock_analysis.baseline.manager import get_baseline_summary


@dataclass
class AppSummaries:
    baseline: dict
    batches: list[dict]
    period_by_key: dict[tuple[int, int, int], dict] = field(default_factory=dict)


_cache: AppSummaries | None = None


def invalidate_summaries() -> None:
    global _cache
    _cache = None


def invalidate_period_summaries() -> None:
    global _cache
    if _cache is not None:
        _cache.period_by_key.clear()


def load_summaries(session: Session) -> AppSummaries:
    global _cache
    if _cache is None:
        _cache = AppSummaries(
            baseline=get_baseline_summary(session),
            batches=list_period_batches(session),
        )
    return _cache


def get_period_summary_cached(
    session: Session,
    lookback_weeks: int | None = None,
    *,
    stock_batch_offset: int = 0,
    sales_batch_offset: int = 0,
) -> dict:
    if lookback_weeks is None:
        lookback_weeks = get_lookback_weeks(session)
    summaries = load_summaries(session)
    key = (lookback_weeks, stock_batch_offset, sales_batch_offset)
    if key not in summaries.period_by_key:
        summaries.period_by_key[key] = build_period_summary(
            session,
            lookback_weeks,
            stock_batch_offset=stock_batch_offset,
            sales_batch_offset=sales_batch_offset,
        )
    return summaries.period_by_key[key]
