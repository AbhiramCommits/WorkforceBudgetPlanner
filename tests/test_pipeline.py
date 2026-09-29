"""End-to-end pipeline tests against a live PostgreSQL instance (skipped when unavailable)."""

from wbp import analytics, generate_data, scenarios
from wbp.db import query_df
from wbp.excel_report import build_report


def test_seed_end_to_end(pg_engine):
    generate_data.main()
    for table, expected in (("sites", 4), ("roles", 17), ("cost_centers", 24)):
        count = query_df(pg_engine, f"SELECT count(*) AS n FROM {table}")["n"].iloc[0]
        assert count == expected
    roster = query_df(pg_engine, "SELECT count(*) AS n FROM headcount_roster")["n"].iloc[0]
    assert roster > 3000
    budget = query_df(pg_engine, "SELECT count(DISTINCT fiscal_month) AS n FROM budget_plan")[
        "n"
    ].iloc[0]
    assert budget == 36


def test_analytics_pipeline_runs(pg_engine):
    analytics.main()
    assert (analytics.MARTS_DIR / "forecast_site.parquet").exists()
    assert (analytics.MARTS_DIR / "v_budget_variance.parquet").exists()
    assert (analytics.MARTS_DIR / "budget_forecast.csv").exists()


def test_scenario_pipeline_and_workbook(pg_engine, tmp_path):
    result = scenarios.run_scenarios(pg_engine)
    comparison = result["comparison"]
    baseline = comparison[comparison["scenario"] == "baseline"].iloc[0]
    assert baseline["delta_total_usd"] == 0.0

    payload = scenarios._build_payload(result)
    path = build_report(payload, tmp_path / "model.xlsx")
    assert path.exists()
    assert path.stat().st_size > 0
