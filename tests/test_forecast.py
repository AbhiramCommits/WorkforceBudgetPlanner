import numpy as np
import pandas as pd
import pytest

from wbp.forecast import (
    backtest,
    forecast_budget,
    forecast_headcount,
    load_params,
    supply_demand_bridge,
)


def _synthetic_series(n_months: int = 48, n_sites: int = 2) -> pd.DataFrame:
    index = pd.date_range("2022-09-01", periods=n_months, freq="MS")
    frames = []
    for site_id in range(1, n_sites + 1):
        t = np.arange(n_months)
        fte = 400 + site_id * 150 + 15 * t + 40 * np.sin(2 * np.pi * t / 12)
        frames.append(pd.DataFrame({"fiscal_month": index, "site_id": site_id, "fte": fte}))
    return pd.concat(frames, ignore_index=True)


def test_load_params_from_yaml():
    params = load_params()
    assert params["forecast"]["horizon"] == 12
    assert params["forecast"]["seasonal_periods"] == 12
    assert params["forecast"]["confidence_levels"] == [0.80, 0.95]
    assert params["budget"]["fringe_rate"] == 0.28


def _linear_frame(n_months: int = 48) -> pd.DataFrame:
    index = pd.date_range("2022-09-01", periods=n_months, freq="MS")
    t = np.arange(n_months)
    noise = np.random.default_rng(7).normal(0, 4, n_months)
    fte = 100.0 + 10.0 * t + noise
    return pd.DataFrame({"fiscal_month": index, "site_id": 1, "fte": fte})


def _seasonal_frame(n_months: int = 48) -> pd.DataFrame:
    index = pd.date_range("2022-09-01", periods=n_months, freq="MS")
    t = np.arange(n_months)
    noise = np.random.default_rng(3).normal(0, 5, n_months)
    fte = 800.0 + 8.0 * t + 60.0 * np.sin(2 * np.pi * t / 12) + noise
    return pd.DataFrame({"fiscal_month": index, "site_id": 1, "fte": fte})


def _seasonal_truth(step: int) -> float:
    return 800.0 + 8.0 * (48 + step) + 60.0 * np.sin(2 * np.pi * (48 + step) / 12)


def test_linear_trend_forecast_within_tolerance():
    out = forecast_headcount(_linear_frame(), method="holtwinters", group_cols=["site_id"])
    expected_h1 = 100.0 + 10.0 * 48
    assert abs(out.iloc[0]["forecast"] - expected_h1) / expected_h1 < 0.05
    expected_h12 = 100.0 + 10.0 * (48 + 11)
    assert abs(out.iloc[11]["forecast"] - expected_h12) / expected_h12 < 0.10


def test_seasonal_forecast_within_tolerance():
    frame = _seasonal_frame()
    for method in ("holtwinters", "sarima"):
        out = forecast_headcount(frame, method=method, group_cols=["site_id"])
        for step in (0, 5, 11):
            truth = _seasonal_truth(step)
            assert abs(out.iloc[step]["forecast"] - truth) / truth < 0.10


def test_confidence_intervals_widen_with_horizon():
    frame = _seasonal_frame()
    for method in ("holtwinters", "sarima"):
        out = forecast_headcount(frame, method=method, group_cols=["site_id"])
        width = out["ci_high_80"] - out["ci_low_80"]
        assert width.iloc[0] > 0
        assert width.iloc[-1] > width.iloc[0]


def test_backtest_mape_finite_and_bounded():
    detail, summary, _ = backtest(_seasonal_frame(n_months=36), windows=3, verbose=False)
    assert summary["mape"].notna().all()
    assert (summary["mape"] < 10).all()
    assert summary["rmse"].notna().all()
    assert len(detail) == 2 * 3


def test_forecast_constant_series_falls_back_gracefully():
    index = pd.date_range("2022-09-01", periods=36, freq="MS")
    frame = pd.DataFrame({"fiscal_month": index, "site_id": 1, "fte": 500.0})
    for method in ("holtwinters", "sarima"):
        out = forecast_headcount(frame, method=method, group_cols=["site_id"])
        assert out["forecast"].notna().all()


def test_forecast_unsupported_method_raises():
    with pytest.raises(ValueError):
        forecast_headcount(_linear_frame(), method="crystal_ball", group_cols=["site_id"])


def test_forecast_headcount_both_methods():
    df = _synthetic_series()
    for method in ("holtwinters", "sarima"):
        out = forecast_headcount(df, method=method, group_cols=["site_id"])
        assert len(out) == 2 * 12
        assert list(out.columns) == [
            "site_id",
            "method",
            "fiscal_month",
            "forecast",
            "ci_low_80",
            "ci_high_80",
            "ci_low_95",
            "ci_high_95",
        ]
        assert out["fiscal_month"].min() == pd.Timestamp("2026-09-01")
        assert out["fiscal_month"].max() == pd.Timestamp("2027-08-01")
        assert (out["forecast"] > 0).all()
        assert (out["ci_low_80"] <= out["forecast"]).all()
        assert (out["forecast"] <= out["ci_high_80"]).all()
        assert (out["ci_low_95"] <= out["ci_low_80"]).all()
        assert (out["ci_high_80"] <= out["ci_high_95"]).all()
        assert (
            out[["forecast", "ci_low_80", "ci_high_80", "ci_low_95", "ci_high_95"]]
            .notna()
            .all()
            .all()
        )


def test_backtest_reports_metrics_and_picks_winner():
    df = _synthetic_series(n_months=36)
    detail, summary, winners = backtest(df, windows=3, verbose=False)
    assert set(summary["method"]) == {"holtwinters", "sarima"}
    assert len(summary) == 4
    assert (summary["mape"] >= 0).all()
    assert (summary["rmse"] >= 0).all()
    assert len(winners) == 2
    assert set(winners.values()) <= {"holtwinters", "sarima"}
    assert len(detail) == 2 * 3 * 2


def test_supply_demand_bridge_formula():
    roster = pd.DataFrame(
        {
            "employee_id": range(1, 11),
            "site_id": [1] * 10,
            "hire_date": [pd.Timestamp("2020-01-01")] * 10,
            "term_date": [None] * 10,
        }
    )
    requisitions = pd.DataFrame(
        {
            "req_id": [1, 2, 3, 4, 5, 6],
            "site_id": [1] * 6,
            "role_id": [1] * 6,
            "opened_date": pd.to_datetime(
                ["2026-06-01", "2026-06-01", "2026-06-01", "2026-08-01", "2026-08-01", "2026-08-01"]
            ),
            "filled_date": [
                pd.Timestamp("2026-07-01"),
                pd.Timestamp("2026-07-01"),
                None,
                None,
                None,
                None,
            ],
            "status": ["filled", "filled", "cancelled", "open", "open", "open"],
        }
    )
    bridge = supply_demand_bridge(
        roster,
        requisitions,
        attrition_rates=pd.Series({1: 0.12}),
        as_of=pd.Timestamp("2026-08-31"),
    )
    first = bridge.iloc[0]
    assert first["current_active"] == 10.0
    assert first["monthly_attrition"] == 0.1
    assert first["expected_req_fills"] == 2.0
    assert first["projected_headcount"] == round(10 - 0.1 + 2.0, 2)
    check = (
        bridge["current_active"] - bridge["cumulative_attrition"] + bridge["cumulative_req_fills"]
    ).round(2)
    assert (check == bridge["projected_headcount"]).all()


def test_forecast_budget_uses_bands_fringe_and_overtime():
    roles = pd.DataFrame(
        {
            "role_id": [1, 2],
            "job_family": ["Fab Operations", "Engineering"],
            "base_annual_usd": [48000, 60000],
        }
    )
    roster = pd.DataFrame(
        {
            "employee_id": range(1, 101),
            "site_id": [1] * 100,
            "role_id": [1] * 60 + [2] * 40,
            "hire_date": [pd.Timestamp("2020-01-01")] * 100,
            "term_date": [None] * 100,
        }
    )
    headcount_forecast = pd.DataFrame(
        {"site_id": [1], "fiscal_month": [pd.Timestamp("2026-09-01")], "forecast": [100.0]}
    )
    family_forecast = pd.DataFrame(
        {
            "site_id": [1, 1],
            "job_family": ["Fab Operations", "Engineering"],
            "fiscal_month": [pd.Timestamp("2026-09-01"), pd.Timestamp("2026-09-01")],
            "forecast": [60.0, 40.0],
        }
    )
    budget = forecast_budget(
        headcount_forecast,
        family_forecast,
        roles,
        roster,
        fringe_rate=0.28,
        overtime_fraction=0.05,
    )
    row = budget.iloc[0]
    expected_labor = 60 * 48000 / 12 + 40 * 60000 / 12
    assert row["labor_usd"] == round(expected_labor, 2)
    assert row["fringe_usd"] == round(expected_labor * 0.28, 2)
    assert row["overtime_usd"] == round(expected_labor * 0.05, 2)
    assert row["total_labor_usd"] == round(
        row["labor_usd"] + row["fringe_usd"] + row["overtime_usd"], 2
    )
