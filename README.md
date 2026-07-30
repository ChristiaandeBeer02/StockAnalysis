# Stock Analysis

Single-user Windows desktop inventory analytics for IQ Retail CSV exports.

## Setup

```bash
pip install -e ".[dev]"
```

## Run

```bash
stock-analysis
```

Or:

```bash
python -m stock_analysis.main
```

## Data location

SQLite database: `%LOCALAPPDATA%\stockAnalysis\stock_data.db`

## Import assumptions

**Baseline and movement periods must align.** The app uses:

1. **Detailed Stockholding** (`sthold2`) — initial baseline and stock takes
2. **Sales_Detail** — net sales quantities for a user-specified date range
3. **PurchasesDetailed** — purchase and return quantities for the same date range

When importing movement data, confirm the **from/to dates** match the period covered by your IQ Retail exports. The app stores these dates on each import batch for audit.

**Practical checks:**

- Compare **Date Printed** on `sthold2` with your intended baseline moment. If Date Printed falls inside the report period, the app warns that slight inaccuracy may occur (ongoing stock hold).
- For historical month-end baselines pulled from a prior month, period-close values are used when Date Printed is after the period end.
- Bridge gaps between month-end baseline and your first weekly movement import by running an additional movement import for the missing days.

**What each file contributes:**

| File | Role |
|------|------|
| sthold2 | Full stockholding snapshot, stock values, baseline quantities |
| Sales_Detail | Period net sales, refunds, department, unit cost |
| PurchasesDetailed | Period purchase and return quantities |

Items that appear only in sthold2 are kept in inventory but marked **No movement data** and are excluded from movement-based dashboard KPIs. That is expected.

**Backdate import (Settings):** Rolls baseline backward by reversing movement for a selected period. Imports are blocked if any SKU would go below zero.

## Phase 1

- Initial baseline import from `sthold2` Detailed Stockholding CSV
- Inventory list with search and deprecated filter
- Home dashboard KPIs

## Phase 2

- Step 2 enrichment: `Sales_Detail` + `PurchasesDetailed` for a confirmed date range
- Ongoing period imports from Home
- Dashboard charts (Qt Charts), understock alerts
- Inventory: dept, weekly sales lookback, item detail with period history

## Phase 3

- Stock take upload and variance comparison (`sthold2` format)
- Reconcile baseline to counted quantities
- Session history and variance audit trail

## Phase 4

- **Sales period (weeks)** spinbox on Home, Inventory, and Reports — sum sales across recent import weeks
- **Reports page** — Slow Moving, ABC analysis, Pivot exploration
- **Excel/PDF export** — understock alerts, reports, item history
- **Per-item charts** — sales and over/under trends on item detail
- **Custom Home dashboard** — toggle KPIs, charts, and alerts in Settings
- **Windows `.exe` packaging** — see Build below

## Build Windows executable

```bash
pip install -e ".[dev]"
build.bat
```

Output: `dist\StockAnalysis.exe`

The packaged app stores its database in `%LOCALAPPDATA%\stockAnalysis\` like the dev install.

## Tests

```bash
pytest
```
