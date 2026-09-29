"""Headcount forecasting, supply/demand bridging, backtesting, and budget translation."""

import warnings
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from scipy.stats import norm
from statsmodels.tsa.holtwinters import ExponentialSmoothing
from statsmodels.tsa.statespace.sarimax import SARIMAX

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PARAMS_PATH = PROJECT_ROOT / "config" / "params.yaml"

DEFAULT_PARAMS = {
    "forecast": {
        "horizon": 12,
        "seasonal_periods": 12,
        "confidence_levels": [0.80, 0.95],
        "backtest_windows": 6,
        "backtest_horizon": 1,
    },
    "budget": {
        "fringe_rate": 0.28,
        "overtime_fraction": 0.05,
        "overtime_premium": 1.5,
        "max_ot_hours_per_head": 60,
    },
    "scenarios": {
        "hiring_freeze": {"start_month": 1},
        "attrition_spike": {"multiplier": 1.75, "months": 6},
        "overtime_shift": {"ot_hours_per_head": 160},
        "accelerated_hiring": {"extra_reqs_per_month": 5, "ramp_cost_per_hire": 5000},
    },
}

SUPPORTED_METHODS = ("holtwinters", "sarima")


def _deep_merge(base: dict, override: dict) -> dict:
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merged[key] = _deep_merge(base[key], value)
        else:
            merged[key] = value
    return merged


@lru_cache(maxsize=1)
def load_params() -> dict:
    if PARAMS_PATH.exists():
        with open(PARAMS_PATH) as fh:
            return _deep_merge(DEFAULT_PARAMS, yaml.safe_load(fh))
    return DEFAULT_PARAMS


def to_datetime_columns(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = df.copy()
    for col in columns:
        if col in out.columns:
            out[col] = pd.to_datetime(out[col])
    return out


def _to_monthly_series(df: pd.DataFrame, group_cols: list[str], value_col: str = "fte") -> dict:
    data = to_datetime_columns(df, ["fiscal_month"])
    series = {}
    for key, sub in data.groupby(group_cols, dropna=False):
        series[key] = (
            sub.set_index("fiscal_month")[value_col].sort_index().asfreq("MS").ffill().bfill()
        )
    return series


def _fit_holtwinters(series: pd.Series, seasonal_periods: int):
    def build(seasonal: bool):
        if seasonal:
            return ExponentialSmoothing(
                series, trend="add", seasonal="add", seasonal_periods=seasonal_periods
            )
        return ExponentialSmoothing(series, trend="add", seasonal=None)

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return build(seasonal=True).fit()
        except Exception:
            return build(seasonal=False).fit()


def _fit_sarima(series: pd.Series, seasonal_periods: int):
    def build(spec: dict):
        return SARIMAX(
            series,
            enforce_stationarity=False,
            enforce_invertibility=False,
            **spec,
        )

    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            return build(
                {
                    "order": (1, 0, 1),
                    "seasonal_order": (1, 0, 1, seasonal_periods),
                    "trend": "c",
                }
            ).fit(disp=False, low_memory=True)
        except Exception:
            return build(
                {
                    "order": (1, 0, 0),
                    "seasonal_order": (1, 0, 0, seasonal_periods),
                    "trend": "c",
                }
            ).fit(disp=False, low_memory=True)


def _naive_seasonal_forecast(series: pd.Series, periods: int, seasonal_periods: int) -> pd.Series:
    seasonal = series.iloc[-seasonal_periods:].values
    values = [seasonal[i % seasonal_periods] for i in range(periods)]
    return pd.Series(values)


def _fit_and_forecast(
    series: pd.Series,
    method: str,
    periods: int,
    seasonal_periods: int,
    levels: list[float],
) -> tuple[pd.Series, dict[float, pd.Series], dict[float, pd.Series]]:
    if method not in SUPPORTED_METHODS:
        raise ValueError(f"unsupported method {method!r}, expected one of {SUPPORTED_METHODS}")

    fitted_prediction = None
    analytic_intervals = False
    try:
        if method == "holtwinters":
            fitted = _fit_holtwinters(series, seasonal_periods)
            forecast = fitted.forecast(periods)
            residual_std = float(fitted.resid.std())
        else:
            fitted = _fit_sarima(series, seasonal_periods)
            fitted_prediction = fitted.get_forecast(periods)
            forecast = fitted_prediction.predicted_mean
            residual_std = float(fitted.resid.std())
            analytic_intervals = not fitted_prediction.conf_int(alpha=0.2).isna().any().any()
    except Exception:
        forecast = _naive_seasonal_forecast(series, periods, seasonal_periods)
        residual_std = float(series.diff(seasonal_periods).std())

    lower, upper = {}, {}
    for level in levels:
        if analytic_intervals:
            interval = fitted_prediction.conf_int(alpha=1 - level)
            lower[level] = interval.iloc[:, 0]
            upper[level] = interval.iloc[:, 1]
        else:
            z = norm.ppf(0.5 + level / 2.0)
            horizon_scale = np.sqrt(np.arange(1, periods + 1))
            lower[level] = forecast - z * residual_std * horizon_scale
            upper[level] = forecast + z * residual_std * horizon_scale
    return forecast, lower, upper


def _forecast_index(series: pd.Series, periods: int) -> pd.DatetimeIndex:
    return pd.date_range(series.index[-1] + pd.offsets.MonthBegin(1), periods=periods, freq="MS")


def _attach_group_columns(frame: pd.DataFrame, group_cols: list[str], key) -> pd.DataFrame:
    values = key if isinstance(key, tuple) else (key,)
    for col, value in zip(group_cols, values):
        frame.insert(0, col, value)
    return frame


def forecast_headcount(
    df: pd.DataFrame,
    periods: int | None = None,
    method: str = "holtwinters",
    group_cols: list[str] | None = None,
    seasonal_periods: int | None = None,
    confidence_levels: list[float] | None = None,
    value_col: str = "fte",
) -> pd.DataFrame:
    params = load_params()["forecast"]
    periods = periods or params["horizon"]
    seasonal_periods = seasonal_periods or params["seasonal_periods"]
    levels = confidence_levels or params["confidence_levels"]
    group_cols = group_cols or ["site_id"]

    frames = []
    for key, series in _to_monthly_series(df, group_cols, value_col).items():
        forecast, lower, upper = _fit_and_forecast(
            series, method, periods, seasonal_periods, levels
        )
        index = _forecast_index(series, periods)
        out = pd.DataFrame(
            {
                "method": method,
                "fiscal_month": index,
                "forecast": np.round(forecast.values, 2),
            }
        )
        for level in levels:
            label = int(round(level * 100))
            out[f"ci_low_{label}"] = np.round(lower[level].values, 2)
            out[f"ci_high_{label}"] = np.round(upper[level].values, 2)
        frames.append(_attach_group_columns(out, group_cols, key))
    return pd.concat(frames, ignore_index=True)


def backtest(
    df: pd.DataFrame,
    group_cols: list[str] | None = None,
    value_col: str = "fte",
    methods: tuple[str, ...] = SUPPORTED_METHODS,
    windows: int | None = None,
    horizon: int | None = None,
    verbose: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    params = load_params()["forecast"]
    group_cols = group_cols or ["site_id"]
    windows = windows or params["backtest_windows"]
    horizon = horizon or params["backtest_horizon"]
    levels = params["confidence_levels"]
    seasonal_periods = params["seasonal_periods"]

    rows = []
    for key, series in _to_monthly_series(df, group_cols, value_col).items():
        for step in range(windows, 0, -1):
            train = series.iloc[:-step]
            actual = float(series.iloc[-step])
            for method in methods:
                forecast, _, _ = _fit_and_forecast(train, method, horizon, seasonal_periods, levels)
                prediction = float(forecast.iloc[horizon - 1])
                error = prediction - actual
                record = {
                    "method": method,
                    "step": step,
                    "actual": round(actual, 2),
                    "prediction": round(prediction, 2),
                    "absolute_error": round(abs(error), 2),
                    "mape": abs(error) / abs(actual) * 100.0,
                    "squared_error": error**2,
                }
                values = key if isinstance(key, tuple) else (key,)
                for col, value in zip(group_cols, values):
                    record[col] = value
                rows.append(record)

    detail = pd.DataFrame(rows)
    summary = (
        detail.groupby(group_cols + ["method"], as_index=False)
        .agg(
            mape=("mape", "mean"),
            rmse=("squared_error", lambda s: float(np.sqrt(s.mean()))),
            windows=("step", "count"),
        )
        .round({"mape": 2, "rmse": 2})
    )

    winners = {}
    for key, sub in summary.groupby(group_cols, dropna=False):
        best = sub.sort_values(["mape", "rmse"]).iloc[0]
        winner_key = key[0] if isinstance(key, tuple) and len(key) == 1 else key
        winners[winner_key] = best["method"]

    if verbose:
        print("\nWalk-forward backtest (MAPE % / RMSE per method):")
        print(summary.to_string(index=False))
        print("\nWinner per series:")
        for key, method in winners.items():
            print(f"  {key}: {method}")
    return detail, summary, winners


def expected_fill_schedule(
    requisitions: pd.DataFrame, horizon: int
) -> dict[tuple[int, int], float]:
    requisitions = to_datetime_columns(
        requisitions, ["opened_date", "target_start_date", "filled_date"]
    )
    closed = requisitions[requisitions["status"].isin(["filled", "cancelled"])]
    fill_rate = (
        closed.assign(filled=(closed["status"] == "filled")).groupby("role_id")["filled"].mean()
    )
    global_fill_rate = float(closed["status"].eq("filled").mean()) if len(closed) else 0.0

    filled = requisitions[requisitions["status"] == "filled"]
    time_to_fill = filled.assign(ttf=(filled["filled_date"] - filled["opened_date"]).dt.days)
    median_ttf = time_to_fill.groupby("role_id")["ttf"].median()
    global_ttf = float(time_to_fill["ttf"].median()) if len(time_to_fill) else 0.0

    open_reqs = requisitions[requisitions["status"] == "open"].copy()
    if open_reqs.empty:
        return {}
    open_reqs["fill_rate"] = open_reqs["role_id"].map(fill_rate).fillna(global_fill_rate)
    open_reqs["fill_month"] = (
        np.ceil(open_reqs["role_id"].map(median_ttf).fillna(global_ttf) / 30.0)
        .clip(1, horizon)
        .astype(int)
    )
    expected = open_reqs.groupby(["site_id", "fill_month"], as_index=False)["fill_rate"].sum()
    return {
        (int(row["site_id"]), int(row["fill_month"])): float(row["fill_rate"])
        for _, row in expected.iterrows()
    }


def supply_demand_bridge(
    roster: pd.DataFrame,
    requisitions: pd.DataFrame,
    attrition_rates: pd.Series,
    as_of,
    horizon: int | None = None,
) -> pd.DataFrame:
    params = load_params()["forecast"]
    horizon = horizon or params["horizon"]

    roster = to_datetime_columns(roster, ["hire_date", "term_date"])
    as_of = pd.Timestamp(as_of)

    active = roster[
        (roster["hire_date"] <= as_of)
        & (roster["term_date"].isna() | (roster["term_date"] > as_of))
    ]
    current_active = active.groupby("site_id").size().rename("current_active")

    expected_map = expected_fill_schedule(requisitions, horizon)

    months = pd.date_range(as_of + pd.offsets.MonthBegin(1), periods=horizon, freq="MS")
    rows = []
    for site_id, headcount in current_active.items():
        rate = float(attrition_rates[site_id])
        cumulative_attrition = 0.0
        cumulative_fills = 0.0
        rounded_attrition = 0.0
        rounded_fills = 0.0
        for step in range(1, horizon + 1):
            monthly_attrition = headcount * rate / 12.0
            req_fills = float(expected_map.get((site_id, step), 0.0))
            cumulative_attrition += monthly_attrition
            cumulative_fills += req_fills
            rounded_attrition = round(cumulative_attrition, 2)
            rounded_fills = round(cumulative_fills, 2)
            headcount = headcount - monthly_attrition + req_fills
            rows.append(
                {
                    "site_id": site_id,
                    "fiscal_month": months[step - 1],
                    "current_active": round(float(current_active[site_id]), 2),
                    "monthly_attrition": round(monthly_attrition, 2),
                    "cumulative_attrition": rounded_attrition,
                    "expected_req_fills": round(req_fills, 2),
                    "cumulative_req_fills": rounded_fills,
                    "projected_headcount": round(
                        float(current_active[site_id]) - rounded_attrition + rounded_fills,
                        2,
                    ),
                }
            )
    return pd.DataFrame(rows)


def blended_salaries(roles: pd.DataFrame, roster: pd.DataFrame) -> pd.DataFrame:
    roster = to_datetime_columns(roster, ["hire_date", "term_date"])
    active = roster[roster["term_date"].isna()]

    mix = active.merge(roles[["role_id", "job_family"]], on="role_id")
    counts = (
        mix.groupby(["site_id", "job_family", "role_id"], as_index=False)
        .size()
        .rename(columns={"size": "n"})
    )
    counts["total"] = counts.groupby(["site_id", "job_family"])["n"].transform("sum")
    counts["weight"] = counts["n"] / counts["total"]
    counts["band"] = counts["role_id"].map(roles.set_index("role_id")["base_annual_usd"])
    return (
        counts.assign(weighted=counts["weight"] * counts["band"])
        .groupby(["site_id", "job_family"], as_index=False)["weighted"]
        .sum()
        .rename(columns={"weighted": "blended_salary_usd"})
    )


def forecast_budget(
    headcount_forecast: pd.DataFrame,
    family_forecast: pd.DataFrame,
    roles: pd.DataFrame,
    roster: pd.DataFrame,
    fringe_rate: float | None = None,
    overtime_fraction: float | None = None,
) -> pd.DataFrame:
    params = load_params()["budget"]
    fringe_rate = params["fringe_rate"] if fringe_rate is None else fringe_rate
    overtime_fraction = (
        params["overtime_fraction"] if overtime_fraction is None else overtime_fraction
    )

    blended = blended_salaries(roles, roster)

    family = to_datetime_columns(family_forecast, ["fiscal_month"]).copy()
    family["family_total"] = family.groupby(["site_id", "fiscal_month"])["forecast"].transform(
        "sum"
    )
    family["share"] = family["forecast"] / family["family_total"]

    headcount = to_datetime_columns(headcount_forecast, ["fiscal_month"]).rename(
        columns={"forecast": "forecast_headcount"}
    )
    joined = headcount.merge(
        family[["site_id", "job_family", "fiscal_month", "share"]],
        on=["site_id", "fiscal_month"],
        how="left",
    )
    joined = joined.merge(blended, on=["site_id", "job_family"], how="left")
    joined["monthly_labor"] = (
        joined["forecast_headcount"] * joined["share"] * joined["blended_salary_usd"] / 12.0
    )

    budget = joined.groupby(["site_id", "fiscal_month"], as_index=False).agg(
        forecast_headcount=("forecast_headcount", "first"),
        labor_usd=("monthly_labor", "sum"),
    )
    budget["labor_usd"] = budget["labor_usd"].round(2)
    budget["fringe_usd"] = (budget["labor_usd"] * fringe_rate).round(2)
    budget["overtime_usd"] = (budget["labor_usd"] * overtime_fraction).round(2)
    budget["total_labor_usd"] = (
        budget["labor_usd"] + budget["fringe_usd"] + budget["overtime_usd"]
    ).round(2)
    return budget
