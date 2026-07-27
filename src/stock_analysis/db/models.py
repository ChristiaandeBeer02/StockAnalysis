"""SQLAlchemy ORM models."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Item(Base):
    __tablename__ = "items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sku: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(512), default="")
    department: Mapped[str | None] = mapped_column(String(32), nullable=True)
    supplier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    unit_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_price: Mapped[float | None] = mapped_column(Float, nullable=True)
    reorder_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    reorder_qty: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_deprecated: Mapped[bool] = mapped_column(Boolean, default=False)
    not_in_turn_report: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    baseline: Mapped["BaselineItem | None"] = relationship(back_populates="item", uselist=False)


class BaselineVersion(Base):
    __tablename__ = "baseline_versions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    version_number: Mapped[int] = mapped_column(Integer, unique=True)
    source_type: Mapped[str] = mapped_column(String(32))
    source_import_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("import_batches.id"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BaselineItem(Base):
    __tablename__ = "baseline_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(Integer, ForeignKey("items.id"), unique=True)
    qty_on_hand: Mapped[float] = mapped_column(Float, default=0.0)
    baseline_version_id: Mapped[int] = mapped_column(Integer, ForeignKey("baseline_versions.id"))
    last_updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_update_source: Mapped[str] = mapped_column(String(32))

    item: Mapped[Item] = relationship(back_populates="baseline")


class BaselineChangeLog(Base):
    __tablename__ = "baseline_change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    item_id: Mapped[int] = mapped_column(Integer, ForeignKey("items.id"), index=True)
    baseline_version_id: Mapped[int] = mapped_column(Integer, ForeignKey("baseline_versions.id"))
    field_changed: Mapped[str] = mapped_column(String(64))
    old_value: Mapped[str | None] = mapped_column(String(128), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(128), nullable=True)
    change_reason: Mapped[str] = mapped_column(String(64))
    source_type: Mapped[str] = mapped_column(String(32))
    source_import_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ImportBatch(Base):
    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    import_type: Mapped[str] = mapped_column(String(32))
    file_name: Mapped[str] = mapped_column(String(512))
    companion_file_name: Mapped[str | None] = mapped_column(String(512), nullable=True)
    imported_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    period_start: Mapped[str | None] = mapped_column(String(32), nullable=True)
    period_end: Mapped[str | None] = mapped_column(String(32), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    deprecated_rows: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="applied")
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class AppState(Base):
    """Singleton-style app flags stored as key-value pairs."""

    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(String(256))


class DepartmentNickname(Base):
    __tablename__ = "department_nicknames"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    nickname: Mapped[str] = mapped_column(String(128), default="")


class PeriodTurnLine(Base):
    __tablename__ = "period_turn_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    import_batch_id: Mapped[int] = mapped_column(Integer, ForeignKey("import_batches.id"), index=True)
    item_id: Mapped[int] = mapped_column(Integer, ForeignKey("items.id"), index=True)
    dept: Mapped[str | None] = mapped_column(String(32), nullable=True)
    supplier: Mapped[str | None] = mapped_column(String(32), nullable=True)
    on_hand: Mapped[float] = mapped_column(Float, default=0.0)
    qty_sold_30: Mapped[float] = mapped_column(Float, default=0.0)
    qty_sold_90: Mapped[float] = mapped_column(Float, default=0.0)
    qty_sold_180: Mapped[float] = mapped_column(Float, default=0.0)
    avg_monthly_sales_3mo: Mapped[float] = mapped_column(Float, default=0.0)
    avg_monthly_sales_6mo: Mapped[float] = mapped_column(Float, default=0.0)
    last_unit_cost: Mapped[float] = mapped_column(Float, default=0.0)
    over_stock_qty_3mo: Mapped[float] = mapped_column(Float, default=0.0)
    over_stock_qty_6mo: Mapped[float] = mapped_column(Float, default=0.0)
    over_stock_value_3mo: Mapped[float] = mapped_column(Float, default=0.0)
    over_stock_value_6mo: Mapped[float] = mapped_column(Float, default=0.0)
    under_stock_qty_3mo: Mapped[float] = mapped_column(Float, default=0.0)
    under_stock_qty_6mo: Mapped[float] = mapped_column(Float, default=0.0)
    under_stock_value_3mo: Mapped[float] = mapped_column(Float, default=0.0)
    under_stock_value_6mo: Mapped[float] = mapped_column(Float, default=0.0)


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    analysis_type: Mapped[str] = mapped_column(String(64))
    import_batch_id: Mapped[int] = mapped_column(Integer, ForeignKey("import_batches.id"))
    stock_take_session_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("stock_take_sessions.id"), nullable=True
    )
    period_start: Mapped[str | None] = mapped_column(String(32), nullable=True)
    period_end: Mapped[str | None] = mapped_column(String(32), nullable=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class StockTakeSession(Base):
    __tablename__ = "stock_take_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    import_batch_id: Mapped[int] = mapped_column(Integer, ForeignKey("import_batches.id"))
    stock_take_date: Mapped[str | None] = mapped_column(String(32), nullable=True)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    total_items: Mapped[int] = mapped_column(Integer, default=0)
    variance_count: Mapped[int] = mapped_column(Integer, default=0)
    shrinkage_value: Mapped[float] = mapped_column(Float, default=0.0)
    overage_value: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(32), default="applied")


class StockTakeLine(Base):
    __tablename__ = "stock_take_lines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(Integer, ForeignKey("stock_take_sessions.id"), index=True)
    item_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("items.id"), nullable=True)
    sku: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(512), default="")
    baseline_qty: Mapped[float] = mapped_column(Float, default=0.0)
    counted_qty: Mapped[float] = mapped_column(Float, default=0.0)
    variance: Mapped[float] = mapped_column(Float, default=0.0)
    variance_value: Mapped[float] = mapped_column(Float, default=0.0)
    line_type: Mapped[str] = mapped_column(String(32))
