"""End-to-end analytics pipeline: SQL views, forecasts, bridge, budget, and marts export."""

from pathlib import Path

import pandas as pd

from .db import get_engine, query_df, run_sql_file
from .forecast import (
    backtest,
    forecast_budget,
    forecast_headcount,
    supply_demand_bridge,
    to_datetime_columns,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VIEWS_PATH = PROJECT_ROOT / "sql" / "views.sql"
MARTS_DIR = PROJECT_ROOT / "data" / "marts"

VIEW_NAMES = [
    "v_monthly_headcount",
    "v_attrition",
    "v_req_aging",
    "v_budget_variance",
    "v_cost_per_head",
]

DATE_COLUMNS = {
    "fiscal_month",
    "hire_date",
    "term_date",
    "opened_date",
    "target_start_date",
    "filled_date",
}


def _normalize_dates(df: pd.DataFrame) -> pd.DataFrame:
    columns = [col for col in df.columns if col in DATE_COLUMNS]
    return to_datetime_columns(df, columns)


def _load_tables(engine) -> dict[str, pd.DataFrame]:
    frames = {}
    for name in VIEW_NAMES:
        frames[name] = _normalize_dates(query_df(engine, f"SELECT * FROM {name}"))
    for table in ("headcount_roster", "requisitions", "roles", "sites"):
        frames[table] = _normalize_dates(query_df(engine, f"SELECT * FROM {table} ORDER BY 1"))
    return frames


def _build_headcount_series(tables: dict[str, pd.DataFrame]) -> tuple[pd.DataFrame, pd.DataFrame]:
    monthly = tables["v_monthly_headcount"].merge(
        tables["roles"][["role_id", "job_family"]], on="role_id", how="left"
    )
    site_series = (
        monthly.groupby(["fiscal_month", "site_id"], as_index=False)["fte"].sum()
    )
    family_series = (
        monthly.groupby(["fiscal_month", "site_id", "job_family"], as_index=False)["fte"].sum()
    )
    return site_series, family_series


def _recent_attrition_rates(tables: dict[str, pd.DataFrame]) -> pd.Series:
    attrition = tables["v_attrition"]
    site_month = attrition.groupby(["fiscal_month", "site_id"], as_index=False).agg(
        terminations=("terminations", "sum"),
        avg_headcount=("avg_headcount", "sum"),
    )
    site_month["annualized_rate"] = site_month["terminations"] / site_month["avg_headcount"] * 12
    cutoff = site_month["fiscal_month"].max() - pd.DateOffset(months=6)
    recent = site_month[site_month["fiscal_month"] > cutoff]
    return recent.groupby("site_id")["annualized_rate"].mean()


def _write_marts(frames: dict[str, pd.DataFrame]) -> None:
    MARTS_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in frames.items():
        normalized = _normalize_dates(df)
        normalized.to_parquet(MARTS_DIR / f"{name}.parquet", index=False)
        normalized.to_csv(MARTS_DIR / f"{name}.csv", index=False)


def main() -> None:
    engine = get_engine()
    run_sql_file(engine, VIEWS_PATH)
    print(f"views created: {', '.join(VIEW_NAMES)}")

    tables = _load_tables(engine)
    site_series, family_series = _build_headcount_series(tables)

    site_forecast = pd.concat(
        [
            forecast_headcount(site_series, method="holtwinters", group_cols=["site_id"]),
            forecast_headcount(site_series, method="sarima", group_cols=["site_id"]),
        ],
        ignore_index=True,
    )
    family_forecast = pd.concat(
        [
            forecast_headcount(
                family_series, method="holtwinters", group_cols=["site_id", "job_family"]
            ),
            forecast_headcount(
                family_series, method="sarima", group_cols=["site_id", "job_family"]
            ),
        ],
        ignore_index=True,
    )

    _, backtest_summary, winners = backtest(
        site_series, group_cols=["site_id"], verbose=True
    )

    site_names = tables["sites"].set_index("site_id")["site_name"]
    winner_rows = []
    for site_id, method in winners.items():
        row = backtest_summary[
            (backtest_summary["site_id"] == site_id) & (backtest_summary["method"] == method)
        ].iloc[0]
        winner_rows.append(
            {
                "site_id": site_id,
                "site_name": site_names[site_id],
                "winning_method": method,
                "mape": row["mape"],
                "rmse": row["rmse"],
            }
        )
    winners_df = pd.DataFrame(winner_rows)
    print("\nWinners applied to site and (site, job family) forecasts:")
    print(winners_df.to_string(index=False))

    as_of = site_series["fiscal_month"].max() + pd.offsets.MonthEnd(0)
    bridge = supply_demand_bridge(
        tables["headcount_roster"],
        tables["requisitions"],
        _recent_attrition_rates(tables),
        as_of,
    )

    winner_method = winners_df.set_index("site_id")["winning_method"]
    site_forecast["winning_method"] = site_forecast["site_id"].map(winner_method)
    family_forecast["winning_method"] = family_forecast["site_id"].map(winner_method)
    site_winner = site_forecast[site_forecast["method"] == site_forecast["winning_method"]].drop(
        columns=["winning_method"]
    )
    family_winner = family_forecast[
        family_forecast["method"] == family_forecast["winning_method"]
    ].drop(columns=["winning_method"])

    budget = forecast_budget(
        site_winner, family_winner, tables["roles"], tables["headcount_roster"]
    )
    budget_summary = (
        budget.groupby("fiscal_month", as_index=False)["total_labor_usd"].sum().round(2)
    )
    print("\nForecast labor budget by month (all sites, USD):")
    print(budget_summary.to_string(index=False))

    marts = {
        "v_monthly_headcount": tables["v_monthly_headcount"],
        "v_attrition": tables["v_attrition"],
        "v_req_aging": tables["v_req_aging"],
        "v_budget_variance": tables["v_budget_variance"],
        "v_cost_per_head": tables["v_cost_per_head"],
        "forecast_site": site_forecast,
        "forecast_site_family": family_forecast,
        "headcount_bridge": bridge,
        "budget_forecast": budget,
        "backtest_results": backtest_summary,
        "backtest_winners": winners_df,
    }
    _write_marts(marts)
    print(f"\nwrote {len(marts)} marts (Parquet + CSV) to {MARTS_DIR}")
    for name, df in marts.items():
        print(f"  {name}: {len(df)} rows")


if __name__ == "__main__":
    main()
