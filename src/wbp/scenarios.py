"""Declarative scenario engine: what-if models applied to the baseline headcount/cost forecast."""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from .db import get_engine, query_df, run_sql_file
from .excel_report import build_report
from .forecast import (
    blended_salaries,
    expected_fill_schedule,
    load_params,
    to_datetime_columns,
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
VIEWS_PATH = PROJECT_ROOT / "sql" / "views.sql"
MARTS_DIR = PROJECT_ROOT / "data" / "marts"
REPORT_PATH = PROJECT_ROOT / "reports" / "workforce_planning_model.xlsx"

CANONICAL_COLUMNS = [
    "site_id",
    "fiscal_month",
    "headcount",
    "monthly_attrition",
    "req_fills",
    "labor_usd",
    "overtime_usd",
    "other_usd",
    "total_labor_usd",
]


@dataclass(frozen=True)
class Scenario:
    kind: str
    params: dict = field(default_factory=dict)
    children: tuple["Scenario", ...] = ()


@dataclass(frozen=True)
class EngineInputs:
    current_active: dict[int, float]
    attrition_rate: dict[int, float]
    family_attrition_rate: dict[tuple[int, str], float]
    family_headcount: dict[tuple[int, str], float]
    fills: dict[tuple[int, int], float]
    blended_salary: dict[int, float]
    fringe_rate: float
    overtime_fraction: float
    overtime_premium: float
    max_ot_hours_per_head: float
    horizon: int
    as_of: pd.Timestamp


def hiring_freeze(start_month: int | None = None, sites=None) -> Scenario:
    if start_month is None:
        start_month = load_params()["scenarios"]["hiring_freeze"]["start_month"]
    return Scenario("hiring_freeze", {"start_month": start_month, "sites": sites})


def attrition_spike(
    multiplier: float | None = None,
    months: int | None = None,
    job_family: str | None = None,
) -> Scenario:
    defaults = load_params()["scenarios"]["attrition_spike"]
    return Scenario(
        "attrition_spike",
        {
            "multiplier": multiplier if multiplier is not None else defaults["multiplier"],
            "months": months if months is not None else defaults["months"],
            "job_family": job_family,
        },
    )


def overtime_shift(
    ot_hours_per_head: float | None = None,
    premium: float | None = None,
    cap_hours_per_head: float | None = None,
) -> Scenario:
    default_hours = load_params()["scenarios"]["overtime_shift"]["ot_hours_per_head"]
    return Scenario(
        "overtime_shift",
        {
            "ot_hours_per_head": (
                ot_hours_per_head if ot_hours_per_head is not None else default_hours
            ),
            "premium": premium,
            "cap_hours_per_head": cap_hours_per_head,
        },
    )


def accelerated_hiring(
    extra_reqs_per_month: float | None = None,
    ramp_cost_per_hire: float | None = None,
) -> Scenario:
    defaults = load_params()["scenarios"]["accelerated_hiring"]
    return Scenario(
        "accelerated_hiring",
        {
            "extra_reqs_per_month": (
                extra_reqs_per_month
                if extra_reqs_per_month is not None
                else defaults["extra_reqs_per_month"]
            ),
            "ramp_cost_per_hire": (
                ramp_cost_per_hire
                if ramp_cost_per_hire is not None
                else defaults["ramp_cost_per_hire"]
            ),
        },
    )


def compose(*scenarios: Scenario) -> Scenario:
    return Scenario("compose", {}, tuple(scenarios))


def _flatten(scenario: Scenario) -> list[Scenario]:
    if scenario.kind == "compose":
        return [modifier for child in scenario.children for modifier in _flatten(child)]
    return [scenario]


def _run_engine(inputs: EngineInputs, scenario: Scenario | None = None) -> pd.DataFrame:
    modifiers = _flatten(scenario) if scenario is not None else []
    months = pd.date_range(
        inputs.as_of + pd.offsets.MonthBegin(1), periods=inputs.horizon, freq="MS"
    )

    freeze_mods = [mod for mod in modifiers if mod.kind == "hiring_freeze"]
    spike_mods = [mod for mod in modifiers if mod.kind == "attrition_spike"]
    overtime_mods = [mod for mod in modifiers if mod.kind == "overtime_shift"]
    accel_mods = [mod for mod in modifiers if mod.kind == "accelerated_hiring"]

    baseline_headcount = None
    if overtime_mods:
        baseline = _run_engine(inputs)
        baseline_headcount = {
            (row["site_id"], row["fiscal_month"]): row["headcount"]
            for _, row in baseline.iterrows()
        }

    rows = []
    for site_id in sorted(inputs.current_active):
        headcount = float(inputs.current_active[site_id])
        for step in range(1, inputs.horizon + 1):
            attrition = headcount * inputs.attrition_rate[site_id] / 12.0
            for mod in spike_mods:
                if step > mod.params["months"]:
                    continue
                if mod.params["job_family"] is None:
                    attrition *= mod.params["multiplier"]
                else:
                    family = mod.params["job_family"]
                    family_hc = inputs.family_headcount.get((site_id, family), 0.0)
                    family_rate = inputs.family_attrition_rate.get((site_id, family), 0.0)
                    attrition += family_hc * family_rate / 12.0 * (mod.params["multiplier"] - 1)

            fills_allowed = True
            for mod in freeze_mods:
                if step > mod.params["start_month"]:
                    sites = mod.params["sites"]
                    if sites is None or site_id in sites:
                        fills_allowed = False
            if overtime_mods:
                fills_allowed = False
            fills = inputs.fills.get((site_id, step), 0.0) if fills_allowed else 0.0

            extra_fills = sum(mod.params["extra_reqs_per_month"] for mod in accel_mods)
            fills += extra_fills

            headcount = headcount - attrition + fills

            labor = headcount * inputs.blended_salary[site_id] / 12.0 * (1 + inputs.fringe_rate)
            overtime = labor * inputs.overtime_fraction
            other = sum(
                mod.params["extra_reqs_per_month"] * mod.params["ramp_cost_per_hire"]
                for mod in accel_mods
            )

            if overtime_mods:
                mod = overtime_mods[0]
                gap = max(0.0, baseline_headcount[(site_id, months[step - 1])] - headcount)
                premium = mod.params["premium"]
                premium = premium if premium is not None else inputs.overtime_premium
                cap = mod.params["cap_hours_per_head"]
                cap = cap if cap is not None else inputs.max_ot_hours_per_head
                needed = gap * mod.params["ot_hours_per_head"]
                covered = min(needed, headcount * cap)
                overtime += covered * inputs.blended_salary[site_id] / 2080.0 * premium

            total = labor + overtime + other
            rows.append(
                {
                    "site_id": site_id,
                    "fiscal_month": months[step - 1],
                    "headcount": round(headcount, 2),
                    "monthly_attrition": round(attrition, 2),
                    "req_fills": round(fills, 2),
                    "labor_usd": round(labor, 2),
                    "overtime_usd": round(overtime, 2),
                    "other_usd": round(other, 2),
                    "total_labor_usd": round(total, 2),
                }
            )
    return pd.DataFrame(rows, columns=CANONICAL_COLUMNS)


def _summarize(frame: pd.DataFrame) -> dict[str, float]:
    by_month = frame.groupby("fiscal_month", as_index=False).agg(
        headcount=("headcount", "sum"),
        labor=("labor_usd", "sum"),
        overtime=("overtime_usd", "sum"),
        other=("other_usd", "sum"),
        total=("total_labor_usd", "sum"),
    )
    average_headcount = float(by_month["headcount"].mean())
    return {
        "ending_headcount": float(by_month["headcount"].iloc[-1]),
        "total_labor_usd": float(by_month["labor"].sum()),
        "total_overtime_usd": float(by_month["overtime"].sum()),
        "total_other_usd": float(by_month["other"].sum()),
        "total_cost_usd": float(by_month["total"].sum()),
        "cost_per_head_usd": float(by_month["total"].sum()) / average_headcount
        if average_headcount
        else 0.0,
    }


def compare_scenarios(baseline: pd.DataFrame, scenarios: dict[str, pd.DataFrame]) -> pd.DataFrame:
    base = _summarize(baseline)
    rows = []
    for name, frame in [("baseline", baseline), *scenarios.items()]:
        summary = _summarize(frame)
        delta = base["total_cost_usd"] - summary["total_cost_usd"]
        rows.append(
            {
                "scenario": name,
                "ending_headcount": round(summary["ending_headcount"], 2),
                "total_labor_usd": round(summary["total_labor_usd"], 2),
                "total_overtime_usd": round(summary["total_overtime_usd"], 2),
                "total_other_usd": round(summary["total_other_usd"], 2),
                "total_cost_usd": round(summary["total_cost_usd"], 2),
                "cost_per_head_usd": round(summary["cost_per_head_usd"], 2),
                "delta_total_usd": round(delta, 2),
                "delta_total_pct": round(delta / base["total_cost_usd"] * 100.0, 2)
                if base["total_cost_usd"]
                else 0.0,
            }
        )
    return pd.DataFrame(rows)


def break_even_analysis(
    inputs: EngineInputs, multipliers: list[float] | None = None
) -> tuple[pd.DataFrame, float | None]:
    multipliers = multipliers or [round(x, 2) for x in np.arange(1.0, 2.51, 0.25)]
    rows = []
    crossover = None
    for multiplier in multipliers:
        backfill = _run_engine(
            inputs, attrition_spike(multiplier=multiplier, months=inputs.horizon)
        )
        overtime_policy = _run_engine(
            inputs,
            compose(
                attrition_spike(multiplier=multiplier, months=inputs.horizon),
                overtime_shift(),
            ),
        )
        backfill_cost = float(backfill["total_labor_usd"].sum())
        overtime_cost = float(overtime_policy["total_labor_usd"].sum())
        rows.append(
            {
                "attrition_multiplier": multiplier,
                "backfill_cost_usd": round(backfill_cost, 2),
                "overtime_policy_cost_usd": round(overtime_cost, 2),
                "ot_minus_backfill_usd": round(overtime_cost - backfill_cost, 2),
            }
        )
        if crossover is None and overtime_cost > backfill_cost:
            crossover = multiplier
    return pd.DataFrame(rows), crossover


def break_even_ot_hours(inputs: EngineInputs, hire_cost: float) -> float:
    headcount = pd.Series(inputs.current_active)
    blended = pd.Series(inputs.blended_salary)
    weighted_blended = float((headcount * blended).sum() / headcount.sum())
    monthly_backfill = (1 + inputs.fringe_rate) / 12.0 + hire_cost / (12.0 * weighted_blended)
    return monthly_backfill * 2080.0 / inputs.overtime_premium


def _load_inputs(engine) -> EngineInputs:
    run_sql_file(engine, VIEWS_PATH)
    roster = to_datetime_columns(
        query_df(engine, "SELECT * FROM headcount_roster"), ["hire_date", "term_date"]
    )
    requisitions = query_df(engine, "SELECT * FROM requisitions")
    roles = query_df(engine, "SELECT role_id, job_family, base_annual_usd FROM roles")
    attrition = to_datetime_columns(query_df(engine, "SELECT * FROM v_attrition"), ["fiscal_month"])
    as_of = pd.Timestamp(
        query_df(
            engine,
            "SELECT (max(fiscal_month) + INTERVAL '1 month - 1 day')::date AS as_of "
            "FROM budget_plan",
        )["as_of"].iloc[0]
    )

    params = load_params()
    horizon = params["forecast"]["horizon"]

    active = roster[
        (roster["hire_date"] <= as_of)
        & (roster["term_date"].isna() | (roster["term_date"] > as_of))
    ]
    current_active = active.groupby("site_id").size().astype(float).to_dict()
    family_headcount = (
        active.merge(roles, on="role_id")
        .groupby(["site_id", "job_family"])
        .size()
        .astype(float)
        .to_dict()
    )

    cutoff = attrition["fiscal_month"].max() - pd.DateOffset(months=6)
    recent = attrition[attrition["fiscal_month"] > cutoff]
    site_agg = recent.groupby("site_id").agg(
        terminations=("terminations", "sum"), avg_headcount=("avg_headcount", "sum")
    )
    site_rate = (site_agg["terminations"] / site_agg["avg_headcount"] * 12).to_dict()
    family_agg = recent.groupby(["site_id", "job_family"]).agg(
        terminations=("terminations", "sum"), avg_headcount=("avg_headcount", "sum")
    )
    family_rate = (family_agg["terminations"] / family_agg["avg_headcount"] * 12).to_dict()

    blended = blended_salaries(roles, roster).set_index(["site_id", "job_family"])[
        "blended_salary_usd"
    ]
    site_blended = {}
    for site_id, site_hc in current_active.items():
        blended_labor = sum(
            blended.get((site_id, family), 0.0) * family_headcount.get((site_id, family), 0.0)
            for family in {key[1] for key in family_headcount if key[0] == site_id}
        )
        site_blended[site_id] = float(blended_labor / site_hc) if site_hc else 0.0

    return EngineInputs(
        current_active=current_active,
        attrition_rate=site_rate,
        family_attrition_rate=family_rate,
        family_headcount=family_headcount,
        fills=expected_fill_schedule(requisitions, horizon),
        blended_salary=site_blended,
        fringe_rate=params["budget"]["fringe_rate"],
        overtime_fraction=params["budget"]["overtime_fraction"],
        overtime_premium=params["budget"]["overtime_premium"],
        max_ot_hours_per_head=params["budget"]["max_ot_hours_per_head"],
        horizon=horizon,
        as_of=as_of,
    )


def _write_marts(frames: dict[str, pd.DataFrame]) -> None:
    MARTS_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in frames.items():
        df.to_parquet(MARTS_DIR / f"{name}.parquet", index=False)
        df.to_csv(MARTS_DIR / f"{name}.csv", index=False)


def _build_payload(result: dict) -> dict:
    inputs = result["inputs"]
    params = load_params()

    aggregate = {}
    for name, frame in {"baseline": result["baseline"], **result["scenarios"]}.items():
        aggregate[name] = (
            frame.groupby("fiscal_month", as_index=False)
            .agg(
                headcount=("headcount", "sum"),
                monthly_attrition=("monthly_attrition", "sum"),
                req_fills=("req_fills", "sum"),
                labor_usd=("labor_usd", "sum"),
                overtime_usd=("overtime_usd", "sum"),
                other_usd=("other_usd", "sum"),
                total_labor_usd=("total_labor_usd", "sum"),
            )
            .round(2)
        )

    headcount = pd.Series(inputs.current_active)
    blended = pd.Series(inputs.blended_salary)
    avg_blended = float((headcount * blended).sum() / headcount.sum())

    assumptions = {
        "fringe_rate": params["budget"]["fringe_rate"],
        "ot_fraction": params["budget"]["overtime_fraction"],
        "ot_premium": params["budget"]["overtime_premium"],
        "attrition_multiplier": params["scenarios"]["attrition_spike"]["multiplier"],
        "spike_months": params["scenarios"]["attrition_spike"]["months"],
        "freeze_start_month": params["scenarios"]["hiring_freeze"]["start_month"],
        "max_ot_hours": params["budget"]["max_ot_hours_per_head"],
        "ot_hours_per_head": params["scenarios"]["overtime_shift"]["ot_hours_per_head"],
        "hire_cost": params["scenarios"]["accelerated_hiring"]["ramp_cost_per_hire"],
        "extra_reqs_per_month": params["scenarios"]["accelerated_hiring"]["extra_reqs_per_month"],
        "sites_count": len(inputs.current_active),
        "avg_blended_salary": round(avg_blended, 2),
    }

    return {
        "baseline": result["baseline"],
        "blended_salary": inputs.blended_salary,
        "sites": result["sites"],
        "scenario_monthly": aggregate,
        "comparison": result["comparison"],
        "break_even_grid": result["break_even_grid"],
        "break_even_multiplier": result["break_even_multiplier"],
        "break_even_hours": result["break_even_hours"],
        "budget_variance": result["budget_variance"],
        "cost_centers": result["cost_centers"],
        "assumptions": assumptions,
        "current_active_total": round(float(headcount.sum()), 2),
    }


def run_scenarios(engine=None) -> dict:
    engine = engine or get_engine()
    inputs = _load_inputs(engine)

    baseline = _run_engine(inputs)
    scenarios = {
        "hiring_freeze": _run_engine(inputs, hiring_freeze()),
        "attrition_spike": _run_engine(inputs, attrition_spike()),
        "overtime_shift": _run_engine(inputs, overtime_shift()),
        "accelerated_hiring": _run_engine(inputs, accelerated_hiring()),
        "spike_plus_freeze": _run_engine(inputs, compose(attrition_spike(), hiring_freeze())),
        "attrition_spike_fab_ops": _run_engine(
            inputs, attrition_spike(job_family="Fab Operations")
        ),
    }

    comparison = compare_scenarios(baseline, scenarios)
    grid, crossover = break_even_analysis(inputs)
    params = load_params()
    break_even_hours = break_even_ot_hours(
        inputs, params["scenarios"]["accelerated_hiring"]["ramp_cost_per_hire"]
    )

    print("\nScenario comparison (12-month horizon):")
    print(comparison.to_string(index=False))
    print("\nBreak-even grid (overtime policy vs backfill hiring):")
    print(grid.to_string(index=False))
    if crossover is not None:
        print(
            f"  -> overtime becomes more expensive than backfill at attrition "
            f"multiplier >= {crossover}"
        )
    else:
        print("  -> overtime never exceeds backfill over the tested multiplier range")
    print(
        f"  -> unit break-even: ~{break_even_hours:.0f} OT hours per head-month make "
        f"overtime equal to backfilling (premium {inputs.overtime_premium}x, "
        f"fringe {inputs.fringe_rate:.0%}, hire cost amortized over 12 months)"
    )

    stacked = pd.concat(
        [
            baseline.assign(scenario="baseline"),
            *[frame.assign(scenario=name) for name, frame in scenarios.items()],
        ],
        ignore_index=True,
    )
    _write_marts(
        {
            "scenario_monthly": stacked,
            "scenario_comparison": comparison,
            "scenario_break_even": grid,
        }
    )
    print(f"\nwrote scenario marts to {MARTS_DIR}")

    sites = query_df(engine, "SELECT site_id, site_name FROM sites ORDER BY site_id")
    cost_centers = query_df(
        engine, "SELECT cc_code, cc_name FROM cost_centers ORDER BY cost_center_id"
    )
    budget_variance = query_df(
        engine,
        "SELECT s.site_name, cc.cc_code, cc.function, v.fiscal_month, "
        "       v.planned_total_usd, v.actual_total_usd, v.total_variance_usd, "
        "       v.total_variance_pct, v.ytd_variance_usd "
        "FROM v_budget_variance v "
        "JOIN cost_centers cc ON cc.cost_center_id = v.cost_center_id "
        "JOIN sites s ON s.site_id = v.site_id "
        "ORDER BY v.cost_center_id, v.fiscal_month",
    )
    budget_variance["fiscal_month"] = pd.to_datetime(budget_variance["fiscal_month"])

    return {
        "baseline": baseline,
        "scenarios": scenarios,
        "comparison": comparison,
        "break_even_grid": grid,
        "break_even_multiplier": crossover,
        "break_even_hours": break_even_hours,
        "inputs": inputs,
        "sites": sites,
        "cost_centers": cost_centers,
        "budget_variance": budget_variance,
    }


def main() -> None:
    result = run_scenarios()
    payload = _build_payload(result)
    path = build_report(payload, REPORT_PATH)
    print(f"\nworkbook written: {path}")


if __name__ == "__main__":
    main()
