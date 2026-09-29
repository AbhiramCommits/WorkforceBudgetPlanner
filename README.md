# Workforce Budget Planner

Forecasts headcount and operating budget for a fictional multi-site semiconductor
manufacturing org (Gresham OR, Roseville CA, Seremban MY, Brno CZ) and tracks actual
spend variance against plan over 36 months of history ending 2026-08-31.

## Stack

Python 3.11, PostgreSQL 15, SQLAlchemy 2.x (Core), pandas, statsmodels, pytest, Docker Compose.

## Setup

1. Create a Python 3.11 virtualenv and install pinned dependencies:

   ```sh
   uv venv --python 3.11 && uv pip install -r requirements.txt
   # or
   python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
   ```

2. (Optional) `cp .env.example .env` — defaults already match `docker-compose.yml`.

3. Start PostgreSQL 15 (host port 5433, persistent volume):

   ```sh
   make db-up
   ```

4. Generate synthetic data (writes `data/raw/*.csv` and loads into PostgreSQL):

   ```sh
   make seed
   ```

5. Inspect the data:

   ```sh
   psql "postgresql://wbp:wbp_password@localhost:5433/workforce_budget" -c "SELECT COUNT(*) FROM headcount_roster;"
   ```

## Data model

`sql/schema.sql` defines: `sites` -> `cost_centers` (6 functions x 4 sites), `roles`,
`headcount_roster`, `requisitions`, `budget_plan`, and `actual_spend` (composite PK on
`cost_center_id + fiscal_month`), with FKs and indexes on join/filter columns.

The generator is deterministic (`random.seed(42)` / `np.random.seed(42)`): ~2,800 active
employees across 4 sites, monthly attrition of 0.8–2.2% varying by site and job family,
seasonal hiring ramps, requisition fill lags of 30–90 days, and actual spend that
deviates from plan by ±3–12%.

## Injected anomalies

- **Overtime overspend**: Seremban runs overtime at ~3.5x normal for 4 consecutive
  months (2025-06 through 2025-09).
- **Chronic non-labor underspend**: Brno Facilities consistently spends only 48–62%
  of its planned non-labor.

## Analytics

`make analytics` (requires a seeded database) runs the full chain:

1. Creates the SQL views in `sql/views.sql`: `v_monthly_headcount`, `v_attrition`,
   `v_req_aging`, `v_budget_variance`, `v_cost_per_head`.
2. Runs 12-month headcount forecasts (Holt-Winters and SARIMA, with 80%/95% CIs) by
   site and by (site, job family), a supply/demand bridge
   (current active - forecast attrition + expected req fills), and a labor-budget
   forecast using role salary bands plus fringe and overtime assumptions.
3. Walk-forward backtests over the last 6 months and picks the winning method per site.
4. Writes all view results and forecast output to `data/marts/` as Parquet + CSV.

Model parameters (horizon, seasonal period, confidence levels, fringe rate, overtime)
live in `config/params.yaml` — nothing is hardcoded in the code.

## Scenario modeling

`make scenarios` runs the scenario engine (`src/wbp/scenarios.py`) and the Excel
deliverable (`src/wbp/excel_report.py`):

- Declarative scenarios: `hiring_freeze`, `attrition_spike`, `overtime_shift`,
  `accelerated_hiring`, composable via `compose()`. Each returns the same canonical
  monthly schema (headcount, attrition, fills, labor, overtime, other, total) as the
  baseline, so scenarios compare directly.
- Side-by-side comparison (ending headcount, labor/OT/other costs, cost per head,
  delta vs baseline in USD and %) plus a numeric break-even: the attrition multiplier
  at which the overtime policy becomes more expensive than backfill hiring.
- Writes `scenario_monthly`, `scenario_comparison`, and `scenario_break_even` marts
  to `data/marts/`, and builds `reports/workforce_planning_model.xlsx` with live
  Excel formulas wired to the `Assumptions` named ranges (fringe rate, OT premium,
  attrition multiplier, hire cost), conditional formatting on variances, a baseline
  vs scenario headcount line chart, a variance-by-cost-center bar chart, freeze
  panes, and autofilters. Change an assumption cell and the model recomputes.

Scenario parameters live in `config/params.yaml` under `scenarios` and `budget`.

## Development

```sh
make test   # pytest
make lint   # ruff
make db-down
```
