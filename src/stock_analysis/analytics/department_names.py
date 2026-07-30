"""Department nickname mapping for display."""

from __future__ import annotations

from sqlalchemy import select, union
from sqlalchemy.orm import Session

from stock_analysis.db.models import DepartmentNickname, Item, PeriodTurnLine


def display_dept(code: str | None, nickname_map: dict[str, str] | None = None) -> str:
    if not code or code in ("—", "Unknown"):
        return code or "—"
    if nickname_map:
        nickname = nickname_map.get(code, "").strip()
        if nickname:
            return nickname
    return code


def list_item_departments(session: Session) -> list[str]:
    rows = session.execute(
        select(Item.department)
        .distinct()
        .where(Item.department.is_not(None))
        .order_by(Item.department)
    ).scalars().all()
    return [row for row in rows if row and row.strip()]


def list_imported_departments(session: Session) -> list[str]:
    item_depts = select(Item.department.label("dept")).where(Item.department.is_not(None))
    turn_depts = select(PeriodTurnLine.dept.label("dept")).where(PeriodTurnLine.dept.is_not(None))
    combined = union(item_depts, turn_depts).subquery()
    rows = session.execute(select(combined.c.dept).distinct().order_by(combined.c.dept)).scalars().all()
    return [row for row in rows if row]


def load_nickname_map(session: Session) -> dict[str, str]:
    rows = session.execute(select(DepartmentNickname)).scalars().all()
    return {row.code: row.nickname for row in rows if row.nickname.strip()}


def save_nicknames(session: Session, mapping: dict[str, str]) -> None:
    existing = {
        row.code: row
        for row in session.execute(select(DepartmentNickname)).scalars().all()
    }
    seen: set[str] = set()
    for code, nickname in mapping.items():
        if not code:
            continue
        seen.add(code)
        cleaned = nickname.strip()
        if not cleaned:
            if code in existing:
                session.delete(existing[code])
            continue
        if code in existing:
            existing[code].nickname = cleaned
        else:
            session.add(DepartmentNickname(code=code, nickname=cleaned))
    for code, row in existing.items():
        if code not in seen:
            session.delete(row)


def update_item_department(session: Session, item_id: int, department: str | None) -> None:
    item = session.get(Item, item_id)
    if not item:
        raise ValueError("Item not found")
    item.department = department or None


def flush_item_departments(session: Session) -> int:
    """Clear department on every item and movement line. Returns items cleared."""
    cleared = 0
    for item in session.scalars(select(Item)):
        if item.department:
            item.department = None
            cleared += 1
    for line in session.scalars(select(PeriodTurnLine)):
        if line.dept:
            line.dept = None
    return cleared
