"""In-memory cache for expensive dashboard summaries."""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from stock_analysis.analytics.dashboard import build_period_summary, list_period_batches
from stock_analysis.analytics.lookback import DEFAULT_LOOKBACK, get_lookback_days
from stock_analysis.baseline.manager import get_baseline_summary


@dataclass
class AppSummaries:
    baseline: dict
    batches: list[dict]
    period_by_key: dict[tuple[int | None, int], dict] = field(default_factory=dict)


_cache: AppSummaries | None = None


def invalidate_summaries() -> None:
    global _cache
    _cache = None


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
    batch_id: int | None = None,
    lookback_days: int | None = None,
) -> dict:
    if lookback_days is None:
        lookback_days = get_lookback_days(session)
    summaries = load_summaries(session)
    key = (batch_id, lookback_days)
    if key not in summaries.period_by_key:
        summaries.period_by_key[key] = build_period_summary(
            session, batch_id, lookback_days
        )
    return summaries.period_by_key[key]
