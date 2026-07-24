"""Immutable baseline change log writes."""

from sqlalchemy.orm import Session

from stock_analysis.db.models import BaselineChangeLog


def log_change(
    session: Session,
    *,
    item_id: int,
    baseline_version_id: int,
    field_changed: str,
    old_value: str | None,
    new_value: str | None,
    change_reason: str,
    source_type: str,
    source_import_id: int | None = None,
) -> None:
    session.add(
        BaselineChangeLog(
            item_id=item_id,
            baseline_version_id=baseline_version_id,
            field_changed=field_changed,
            old_value=old_value,
            new_value=new_value,
            change_reason=change_reason,
            source_type=source_type,
            source_import_id=source_import_id,
        )
    )
