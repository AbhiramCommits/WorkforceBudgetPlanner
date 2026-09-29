"""SQL view tests against a transient PostgreSQL database with hand-computed fixtures.

Active-in-month boundary cases covered explicitly:
- hired mid-month (2023-09-15)  -> active in that month
- hired exactly on month end (2023-09-30) -> active in that month
- hired on the first day of the next month (2023-10-01) -> not active in Sep, active in Oct
- terminated mid-month (2023-09-15) -> not active in that month
- term_date exactly equal to month end (2023-09-30) -> not active in that month
- term_date exactly equal to next month start (2023-10-01) -> active in Sep, not active in Oct
"""

from pathlib import Path

import pandas as pd
import pytest

from wbp.db import insert_dataframe, query_df, run_sql_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VIEWS_PATH = PROJECT_ROOT / "sql" / "views.sql"


def _fixtures() -> dict[str, pd.DataFrame]:
    return {
        "sites": pd.DataFrame(
            [
                {"site_id": 1, "site_name": "Alpha", "region": "Americas", "country": "US"},
                {"site_id": 2, "site_name": "Beta", "region": "EMEA", "country": "DE"},
            ]
        ),
        "roles": pd.DataFrame(
            [
                {
                    "role_id": 1,
                    "role_name": "Fab Operator",
                    "job_family": "Fab Operations",
                    "salary_band": "OP1",
                    "base_annual_usd": 42000,
                },
                {
                    "role_id": 2,
                    "role_name": "Process Engineer",
                    "job_family": "Engineering",
                    "salary_band": "E2",
                    "base_annual_usd": 95000,
                },
            ]
        ),
        "cost_centers": pd.DataFrame(
            [
                {
                    "cost_center_id": 1,
                    "site_id": 1,
                    "cc_code": "ALP-FAB",
                    "cc_name": "Alpha Fab",
                    "function": "Fab Operations",
                },
                {
                    "cost_center_id": 2,
                    "site_id": 2,
                    "cc_code": "BET-ENG",
                    "cc_name": "Beta Eng",
                    "function": "Engineering",
                },
            ]
        ),
        "headcount_roster": pd.DataFrame(
            {
                "employee_id": [1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008],
                "site_id": [1, 1, 1, 1, 1, 1, 2, 2],
                "cost_center_id": [1, 1, 1, 1, 1, 1, 2, 2],
                "role_id": [1, 1, 2, 2, 1, 1, 1, 2],
                "shift": ["A", "B", "Days", "Days", "C", "A", "B", "Days"],
                "hire_date": pd.to_datetime(
                    [
                        "2023-01-01",
                        "2023-09-15",
                        "2023-01-01",
                        "2023-01-01",
                        "2023-09-30",
                        "2023-10-01",
                        "2023-01-01",
                        "2023-01-01",
                    ]
                ),
                "term_date": pd.to_datetime(
                    [None, None, "2023-09-15", "2023-09-30", None, None, "2023-10-01", None]
                ),
                "fte": [1.0] * 8,
                "annual_salary_usd": [42000, 42000, 95000, 95000, 42000, 42000, 42000, 95000],
            }
        ),
        "requisitions": pd.DataFrame(
            {
                "req_id": [1, 2, 3, 4, 5, 6, 7, 8, 9],
                "site_id": [1, 1, 1, 1, 1, 1, 1, 2, 2],
                "cost_center_id": [1, 1, 1, 1, 1, 1, 1, 2, 2],
                "role_id": [1, 1, 2, 1, 1, 2, 1, 1, 2],
                "opened_date": pd.to_datetime(
                    [
                        "2023-05-01",
                        "2023-05-01",
                        "2023-05-01",
                        "2023-11-10",
                        "2023-10-01",
                        "2023-09-01",
                        "2023-08-01",
                        "2023-11-20",
                        "2023-05-01",
                    ]
                ),
                "target_start_date": pd.to_datetime(
                    [
                        "2023-06-01",
                        "2023-06-20",
                        "2023-07-10",
                        "2023-12-10",
                        "2023-11-01",
                        "2023-10-01",
                        "2023-09-01",
                        "2023-12-20",
                        "2023-06-11",
                    ]
                ),
                "filled_date": pd.to_datetime(
                    [
                        "2023-06-01",
                        "2023-06-20",
                        "2023-07-10",
                        None,
                        None,
                        None,
                        None,
                        None,
                        "2023-06-11",
                    ]
                ),
                "status": [
                    "filled",
                    "filled",
                    "filled",
                    "open",
                    "open",
                    "open",
                    "open",
                    "open",
                    "filled",
                ],
            }
        ),
        "budget_plan": pd.DataFrame(
            {
                "cost_center_id": [1, 1, 1, 2, 2, 2],
                "fiscal_month": pd.to_datetime(
                    [
                        "2023-09-01",
                        "2023-10-01",
                        "2023-11-01",
                        "2023-09-01",
                        "2023-10-01",
                        "2023-11-01",
                    ]
                ),
                "planned_headcount": [2.0, 2.0, 2.0, 1.0, 1.0, 1.0],
                "planned_labor_usd": [1000.0, 2000.0, 3000.0, 800.0, 800.0, 800.0],
                "planned_nonlabor_usd": [500.0, 0.0, 0.0, 100.0, 0.0, 0.0],
            }
        ),
        "actual_spend": pd.DataFrame(
            {
                "cost_center_id": [1, 1, 1, 2, 2, 2],
                "fiscal_month": pd.to_datetime(
                    [
                        "2023-09-01",
                        "2023-10-01",
                        "2023-11-01",
                        "2023-09-01",
                        "2023-10-01",
                        "2023-11-01",
                    ]
                ),
                "actual_labor_usd": [1100.0, 1800.0, 3000.0, 700.0, 780.0, 750.0],
                "actual_overtime_usd": [100.0, 50.0, 100.0, 50.0, 20.0, 30.0],
                "actual_nonlabor_usd": [550.0, 50.0, 100.0, 150.0, 0.0, 0.0],
            }
        ),
    }


@pytest.fixture()
def seeded_db(test_db_engine):
    run_sql_file(test_db_engine, VIEWS_PATH)
    for table, frame in _fixtures().items():
        insert_dataframe(test_db_engine, table, frame)
    return test_db_engine


def test_monthly_headcount_active_in_month_boundaries(seeded_db):
    monthly = query_df(
        seeded_db,
        "SELECT fiscal_month, site_id, SUM(headcount) AS hc "
        "FROM v_monthly_headcount GROUP BY 1, 2 ORDER BY 1, 2",
    )
    got = [
        (pd.Timestamp(row.fiscal_month), int(row.site_id), int(row.hc))
        for row in monthly.itertuples()
    ]
    expected = [
        (pd.Timestamp("2023-09-01"), 1, 3),
        (pd.Timestamp("2023-09-01"), 2, 2),
        (pd.Timestamp("2023-10-01"), 1, 4),
        (pd.Timestamp("2023-10-01"), 2, 1),
        (pd.Timestamp("2023-11-01"), 1, 4),
        (pd.Timestamp("2023-11-01"), 2, 1),
    ]
    assert got == expected

    sep_role2 = query_df(
        seeded_db,
        "SELECT SUM(headcount) AS hc FROM v_monthly_headcount "
        "WHERE fiscal_month = '2023-09-01' AND site_id = 1 AND role_id = 2",
    )["hc"].iloc[0]
    assert pd.isna(sep_role2) or sep_role2 == 0

    oct_role1 = query_df(
        seeded_db,
        "SELECT SUM(headcount) AS hc FROM v_monthly_headcount "
        "WHERE fiscal_month = '2023-10-01' AND site_id = 2 AND role_id = 1",
    )["hc"].iloc[0]
    assert pd.isna(oct_role1) or oct_role1 == 0


def test_attrition_view_hand_computed(seeded_db):
    def row(site_id, family, month):
        return query_df(
            seeded_db,
            "SELECT * FROM v_attrition WHERE site_id = :site AND job_family = :fam "
            "AND fiscal_month = :month",
            params={"site": site_id, "fam": family, "month": month},
        ).iloc[0]

    sep_eng = row(1, "Engineering", "2023-09-01")
    assert sep_eng["avg_headcount"] == 1.0
    assert sep_eng["terminations"] == 2
    assert sep_eng["annualized_rate"] == 24.0

    sep_fab = row(1, "Fab Operations", "2023-09-01")
    assert sep_fab["avg_headcount"] == 2.0
    assert sep_fab["terminations"] == 0
    assert sep_fab["annualized_rate"] == 0.0

    oct_fab_beta = row(2, "Fab Operations", "2023-10-01")
    assert oct_fab_beta["avg_headcount"] == 0.5
    assert oct_fab_beta["terminations"] == 1
    assert oct_fab_beta["annualized_rate"] == 24.0

    oct_eng = row(1, "Engineering", "2023-10-01")
    assert pd.isna(oct_eng["annualized_rate"])
    assert oct_eng["annualized_rate_3mo_avg"] == 24.0


def test_req_aging_view_buckets_and_median(seeded_db):
    aging = query_df(seeded_db, "SELECT * FROM v_req_aging ORDER BY site_id")
    site1 = aging[aging["site_id"] == 1].iloc[0]
    assert site1["open_0_30_days"] == 1
    assert site1["open_31_60_days"] == 1
    assert site1["open_61_90_days"] == 1
    assert site1["open_90_plus_days"] == 1
    assert site1["open_total"] == 4
    assert site1["closed_total"] == 3
    assert site1["median_time_to_fill_days"] == 50.0

    site2 = aging[aging["site_id"] == 2].iloc[0]
    assert site2["open_0_30_days"] == 1
    assert site2["open_total"] == 1
    assert site2["closed_total"] == 1
    assert site2["median_time_to_fill_days"] == 41.0


def test_budget_variance_view_overspend_is_negative(seeded_db):
    def row(cc_id, month):
        return query_df(
            seeded_db,
            "SELECT * FROM v_budget_variance WHERE cost_center_id = :cc "
            "AND fiscal_month = :month",
            params={"cc": cc_id, "month": month},
        ).iloc[0]

    sep = row(1, "2023-09-01")
    assert sep["planned_total_usd"] == 1500.0
    assert sep["actual_total_usd"] == 1750.0
    assert sep["total_variance_usd"] == -250.0
    assert sep["total_variance_pct"] == pytest.approx(-16.67, abs=0.01)
    assert sep["ytd_variance_usd"] == -250.0

    oct_row = row(1, "2023-10-01")
    assert oct_row["total_variance_usd"] == 100.0
    assert oct_row["ytd_variance_usd"] == -150.0

    nov = row(1, "2023-11-01")
    assert nov["total_variance_usd"] == -200.0
    assert nov["ytd_variance_usd"] == -350.0

    favorable = row(2, "2023-11-01")
    assert favorable["total_variance_usd"] == 20.0
    assert favorable["total_variance_pct"] > 0


def test_cost_per_head_view(seeded_db):
    cph = query_df(seeded_db, "SELECT * FROM v_cost_per_head ORDER BY site_id, fiscal_month")
    sep = cph[(cph["site_id"] == 1) & (cph["fiscal_month"].astype(str) == "2023-09-01")].iloc[0]
    assert sep["avg_headcount"] == 3.0
    assert sep["total_spend_usd"] == 1750.0
    assert sep["cost_per_head_usd"] == pytest.approx(583.33, abs=0.01)

    sep2 = cph[(cph["site_id"] == 2) & (cph["fiscal_month"].astype(str) == "2023-09-01")].iloc[0]
    assert sep2["avg_headcount"] == 2.0
    assert sep2["total_spend_usd"] == 900.0
    assert sep2["cost_per_head_usd"] == 450.0
