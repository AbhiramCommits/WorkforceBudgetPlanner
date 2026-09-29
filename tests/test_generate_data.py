from datetime import date

import pandas as pd

from wbp.generate_data import (
    HISTORY_MONTHS,
    NONLABOR_UNDERSPEND_CC_ID,
    OVERTIME_ANOMALY_MONTHS,
    OVERTIME_ANOMALY_SITE_ID,
    _month_end,
    _month_starts,
    generate_datasets,
)


def test_generation_is_deterministic():
    first = generate_datasets()
    second = generate_datasets()
    for name in first:
        pd.testing.assert_frame_equal(first[name], second[name])


def test_dimension_tables():
    datasets = generate_datasets()
    assert len(datasets["sites"]) == 4
    assert len(datasets["cost_centers"]) == 24
    assert len(datasets["roles"]) >= 12
    assert set(datasets["sites"]["country"]) == {"US", "MY", "CZ"}
    assert set(datasets["cost_centers"]["function"]) == {
        "Fab Operations",
        "Test & Assembly",
        "Facilities",
        "Quality",
        "Supply Chain",
        "Engineering",
    }


def test_foreign_key_integrity():
    datasets = generate_datasets()
    site_ids = set(datasets["sites"]["site_id"])
    cc_ids = set(datasets["cost_centers"]["cost_center_id"])
    role_ids = set(datasets["roles"]["role_id"])

    roster = datasets["headcount_roster"]
    assert set(roster["site_id"]) <= site_ids
    assert set(roster["cost_center_id"]) <= cc_ids
    assert set(roster["role_id"]) <= role_ids

    requisitions = datasets["requisitions"]
    assert set(requisitions["site_id"]) <= site_ids
    assert set(requisitions["cost_center_id"]) <= cc_ids
    assert set(requisitions["role_id"]) <= role_ids

    for name in ("budget_plan", "actual_spend"):
        assert set(datasets[name]["cost_center_id"]) <= cc_ids


def test_enum_values():
    datasets = generate_datasets()
    assert set(datasets["headcount_roster"]["shift"]) <= {"A", "B", "C", "Days"}
    assert set(datasets["requisitions"]["status"]) <= {"open", "filled", "cancelled", "on_hold"}


def test_history_span_and_composite_keys():
    datasets = generate_datasets()
    starts = _month_starts()
    assert starts[0] == date(2023, 9, 1)
    assert starts[-1] == date(2026, 8, 1)
    assert len(starts) == HISTORY_MONTHS == 36

    for name in ("budget_plan", "actual_spend"):
        df = datasets[name]
        assert len(df) == 24 * HISTORY_MONTHS
        assert set(df["fiscal_month"]) == set(starts)
        assert df.duplicated(subset=["cost_center_id", "fiscal_month"]).sum() == 0


def test_ending_headcount_is_roughly_2800():
    roster = generate_datasets()["headcount_roster"]
    active = roster["term_date"].isna().sum()
    assert 2600 <= active <= 3000


def test_monthly_attrition_within_band():
    roster = generate_datasets()["headcount_roster"]
    hire_dt = pd.to_datetime(roster["hire_date"])
    term_dt = pd.to_datetime(roster["term_date"])
    for month_start in _month_starts():
        start_ts = pd.Timestamp(month_start)
        end_ts = pd.Timestamp(_month_end(month_start))
        active_at_start = (hire_dt <= start_ts) & (term_dt.isna() | (term_dt >= start_ts))
        termed_during = (term_dt >= start_ts) & (term_dt <= end_ts)
        rate = termed_during.sum() / active_at_start.sum()
        assert 0.004 <= rate <= 0.030


def test_requisition_fill_lags_between_30_and_90_days():
    requisitions = generate_datasets()["requisitions"]
    filled = requisitions[requisitions["status"] == "filled"]
    assert len(filled) > 0
    lags = (filled["filled_date"] - filled["opened_date"]).map(lambda delta: delta.days)
    assert lags.between(30, 90).all()


def test_overtime_anomaly_at_seremban():
    datasets = generate_datasets()
    cost_centers = datasets["cost_centers"].set_index("cost_center_id")
    spend = datasets["actual_spend"].copy()
    spend["site_id"] = spend["cost_center_id"].map(cost_centers["site_id"])
    spend["ot_ratio"] = spend["actual_overtime_usd"] / spend["actual_labor_usd"]

    seremban = spend[spend["site_id"] == OVERTIME_ANOMALY_SITE_ID]
    anomaly = seremban[seremban["fiscal_month"].isin(OVERTIME_ANOMALY_MONTHS)]
    normal = seremban[
        seremban["fiscal_month"].isin(
            [date(2025, 4, 1), date(2025, 5, 1), date(2025, 10, 1), date(2025, 11, 1)]
        )
    ]
    assert anomaly["ot_ratio"].mean() > 2.0 * normal["ot_ratio"].mean()


def test_nonlabor_underspend_is_chronic():
    datasets = generate_datasets()
    plan = datasets["budget_plan"]
    actual = datasets["actual_spend"]
    merged = plan.merge(
        actual, on=["cost_center_id", "fiscal_month"], suffixes=("_plan", "_actual")
    )
    cc = merged[merged["cost_center_id"] == NONLABOR_UNDERSPEND_CC_ID]
    assert len(cc) == HISTORY_MONTHS
    ratio = cc["actual_nonlabor_usd"] / cc["planned_nonlabor_usd"]
    assert (ratio < 0.7).all()
    assert (ratio > 0.4).all()
