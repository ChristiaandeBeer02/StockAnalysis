"""Tests for Sales_Detail and PurchasesDetailed movement parser."""

from pathlib import Path

import pytest

from stock_analysis.importers.movement_parser import merge_movement_reports

FIXTURES = Path(__file__).resolve().parents[1] / "test_imports"
SALES = FIXTURES / "Sales_Detail_sample.csv"
PURCHASES = FIXTURES / "PurchasesDetailed_sample.csv"


@pytest.fixture(scope="module", autouse=True)
def _ensure_fixtures() -> None:
    from tests.helpers.import_snapshot import write_fixture_csvs

    write_fixture_csvs()


def test_merge_movement_reports_sample() -> None:
    result = merge_movement_reports(SALES, PURCHASES)
    codes = {row.code for row in result.rows}
    assert "BASE001" in codes
    assert "BASE002" in codes
    assert "MOVE001" in codes

    base001 = next(row for row in result.rows if row.code == "BASE001")
    assert base001.net_sales_qty == pytest.approx(2.0)
    assert base001.net_sales_revenue == pytest.approx(10.0)
    assert base001.gross_profit == pytest.approx(0.0)
    assert base001.gross_margin_pct == pytest.approx(0.0)

    base002 = next(row for row in result.rows if row.code == "BASE002")
    assert base002.net_sales_qty == pytest.approx(5.0)
    assert base002.net_purchases_qty == pytest.approx(3.0)

    move001 = next(row for row in result.rows if row.code == "MOVE001")
    assert move001.net_sales_qty == pytest.approx(0.0)
    assert move001.net_purchases_qty == pytest.approx(3.0)


def test_merge_includes_purchases_only_sku() -> None:
    result = merge_movement_reports(SALES, PURCHASES)
    assert any(row.code == "MOVE001" for row in result.rows)


def test_parse_sales_monetary_with_profit(tmp_path: Path) -> None:
    sales_path = tmp_path / "sales.csv"
    purchases_path = tmp_path / "purchases.csv"
    sales_path.write_text(
        '"CODE","DEPARTMENT","MAINITEM","Descript","AvrgCost","GenCode","PurchaseOr","OnHand",'
        '"Regular_SU","SalesOrder","WIPQty","LBOnhand","Subdepartm","Category","Range","Cycle",'
        '"Sales","SalesQty","SalesCost","Refunds","RefundsQty","RefundsCost","NettSales",'
        '"NettSalesQuantity","NettCost","Profit","Purchases","Returns","VAT"\n'
        '"PROF001","249","","Profit Test Item",10.00,"",0.00,0.00,"",0.00,0.00,0.00,"","","","",'
        '100.00,5.00,50.00,0.00,0.00,0.00,100.00,5.00,60.00,40.00,0.00,0.00,0.00\n',
        encoding="utf-8",
    )
    purchases_path.write_text(
        '"Code","Department","MainItem","Sales","Units","SalesCost","Refunds","RefundsQty",'
        '"RefundsCost","NettSales","NettCost","Profit","Purchases","RETURNS","PurchasesQT",'
        '"RETURNSQT","NettPurchases","NettPurchases_VAT","VAT"\n',
        encoding="utf-8",
    )

    result = merge_movement_reports(sales_path, purchases_path)
    row = next(r for r in result.rows if r.code == "PROF001")
    assert row.net_sales_revenue == pytest.approx(100.0)
    assert row.net_sales_cost == pytest.approx(60.0)
    assert row.gross_profit == pytest.approx(40.0)
    assert row.gross_margin_pct == pytest.approx(40.0)


def test_parse_sales_uses_subdepartm_not_department(tmp_path: Path) -> None:
    sales_path = tmp_path / "sales.csv"
    purchases_path = tmp_path / "purchases.csv"
    sales_path.write_text(
        '"CODE","DEPARTMENT","MAINITEM","Descript","AvrgCost","GenCode","PurchaseOr","OnHand",'
        '"Regular_SU","SalesOrder","WIPQty","LBOnhand","Subdepartm","Category","Range","Cycle",'
        '"Sales","SalesQty","SalesCost","Refunds","RefundsQty","RefundsCost","NettSales",'
        '"NettSalesQuantity","NettCost","Profit","Purchases","Returns","VAT"\n'
        '"DEPT001","249","","Dept Test Item",5.00,"",0.00,0.00,"",0.00,0.00,0.00,"T001","","","",'
        '10.00,1.00,5.00,0.00,0.00,0.00,10.00,1.00,5.00,5.00,0.00,0.00,0.00\n'
        '"NODEPT","999","","No Dept Item",5.00,"",0.00,0.00,"",0.00,0.00,0.00,"","","","",'
        '10.00,1.00,5.00,0.00,0.00,0.00,10.00,1.00,5.00,5.00,0.00,0.00,0.00\n',
        encoding="utf-8",
    )
    purchases_path.write_text(
        '"Code","Department","MainItem","Sales","Units","SalesCost","Refunds","RefundsQty",'
        '"RefundsCost","NettSales","NettCost","Profit","Purchases","RETURNS","PurchasesQT",'
        '"RETURNSQT","NettPurchases","NettPurchases_VAT","VAT"\n',
        encoding="utf-8",
    )

    result = merge_movement_reports(sales_path, purchases_path)
    by_code = {row.code: row for row in result.rows}
    assert by_code["DEPT001"].department == "T001"
    assert by_code["NODEPT"].department == ""
