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

**Export all files from the same period.** For accurate results, run your IQ Retail exports on the same day and for the same reporting window:

1. **Detailed Stockholding** (`sthold2`) — initial baseline and stock takes
2. **Stock Turn – Over Stocking** (`IQStockTurn.csv`)
3. **Stock Turn – Under Stocking** (`IQStockTurnunder.csv`)

The app does not currently verify that these files match. If the baseline and turn reports are from different dates or periods, on-hand quantities and analytics may be inconsistent.

**Practical check:** Compare the **Date Printed** line at the top of each CSV before importing. On production exports, sthold2 includes date and time (e.g. `Date Printed :14/07/2026 11:40:03`); turn reports include date only (e.g. `Date Printed: 14/07/2026`). All three should show the same print date.

**What each file contributes:**

| File | Role |
|------|------|
| sthold2 | Full stockholding snapshot, stock values, baseline quantities |
| IQStockTurn | Sales velocity, over-stock, department, supplier, unit cost |
| IQStockTurnunder | Under-stock metrics (required alongside the over report) |

Items that appear only in sthold2 are kept in inventory but marked **No turn data** and are excluded from turn-based dashboard KPIs. That is expected.

**Future improvement (not implemented):** The app could warn at import time when `Date Printed` or report periods differ across files. For now, alignment is the client's responsibility when exporting from IQ Retail.

## Phase 1

- Initial baseline import from `sthold2` Detailed Stockholding CSV
- Inventory list with search and deprecated filter
- Home dashboard KPIs

## Phase 2

- Step 2 enrichment: `IQStockTurn.csv` + `IQStockTurnunder.csv`
- Ongoing period imports from Home
- Dashboard charts (Qt Charts), understock alerts
- Inventory: dept, sold 90d, item detail with period history

## Phase 3

- Stock take upload and variance comparison (`sthold2` format)
- Reconcile baseline to counted quantities
- Session history and variance audit trail

## Phase 4

- **Period selector** on Home and Reports — switch between enrichment and period imports
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
