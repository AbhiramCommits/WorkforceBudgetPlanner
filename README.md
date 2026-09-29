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

## Development

```sh
make test   # pytest
make lint   # ruff
make db-down
```
