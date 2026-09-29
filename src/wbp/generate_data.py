"""Synthetic headcount, requisition, budget, and actual-spend data generator."""

import math
import random
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sqlalchemy.exc import OperationalError

from .db import get_engine, insert_dataframe, run_sql_file

SEED = 42
HISTORY_MONTHS = 36
HISTORY_START = date(2023, 9, 1)
HISTORY_END = date(2026, 8, 31)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = PROJECT_ROOT / "sql" / "schema.sql"
DATA_DIR = PROJECT_ROOT / "data" / "raw"

LOAD_ORDER = [
    "sites",
    "roles",
    "cost_centers",
    "headcount_roster",
    "requisitions",
    "budget_plan",
    "actual_spend",
]

SITES = [
    {"site_id": 1, "site_name": "Gresham", "region": "Americas", "country": "US"},
    {"site_id": 2, "site_name": "Roseville", "region": "Americas", "country": "US"},
    {"site_id": 3, "site_name": "Seremban", "region": "APAC", "country": "MY"},
    {"site_id": 4, "site_name": "Brno", "region": "EMEA", "country": "CZ"},
]

FUNCTIONS = [
    "Fab Operations",
    "Test & Assembly",
    "Facilities",
    "Quality",
    "Supply Chain",
    "Engineering",
]
FUNCTION_CODES = {
    "Fab Operations": "FAB",
    "Test & Assembly": "TNA",
    "Facilities": "FAC",
    "Quality": "QLT",
    "Supply Chain": "SCM",
    "Engineering": "ENG",
}
SITE_CODES = {1: "GRS", 2: "RSV", 3: "SRB", 4: "BRN"}

ROLES = [
    {
        "role_id": 1,
        "role_name": "Fab Operator",
        "job_family": "Fab Operations",
        "salary_band": "OP1",
        "base_annual_usd": 42000,
    },
    {
        "role_id": 2,
        "role_name": "Senior Fab Operator",
        "job_family": "Fab Operations",
        "salary_band": "OP2",
        "base_annual_usd": 48000,
    },
    {
        "role_id": 3,
        "role_name": "Equipment Technician",
        "job_family": "Fab Operations",
        "salary_band": "T2",
        "base_annual_usd": 68000,
    },
    {
        "role_id": 4,
        "role_name": "Operations Manager",
        "job_family": "Fab Operations",
        "salary_band": "M1",
        "base_annual_usd": 138000,
    },
    {
        "role_id": 5,
        "role_name": "Test Operator",
        "job_family": "Test & Assembly",
        "salary_band": "OP1",
        "base_annual_usd": 40000,
    },
    {
        "role_id": 6,
        "role_name": "Test Technician",
        "job_family": "Test & Assembly",
        "salary_band": "T2",
        "base_annual_usd": 62000,
    },
    {
        "role_id": 7,
        "role_name": "Test Engineer",
        "job_family": "Test & Assembly",
        "salary_band": "E2",
        "base_annual_usd": 92000,
    },
    {
        "role_id": 8,
        "role_name": "Facilities Technician",
        "job_family": "Facilities",
        "salary_band": "T2",
        "base_annual_usd": 60000,
    },
    {
        "role_id": 9,
        "role_name": "Facilities Engineer",
        "job_family": "Facilities",
        "salary_band": "E2",
        "base_annual_usd": 90000,
    },
    {
        "role_id": 10,
        "role_name": "Quality Inspector",
        "job_family": "Quality",
        "salary_band": "OP2",
        "base_annual_usd": 45000,
    },
    {
        "role_id": 11,
        "role_name": "Quality Engineer",
        "job_family": "Quality",
        "salary_band": "E2",
        "base_annual_usd": 93000,
    },
    {
        "role_id": 12,
        "role_name": "Planner",
        "job_family": "Supply Chain",
        "salary_band": "P2",
        "base_annual_usd": 72000,
    },
    {
        "role_id": 13,
        "role_name": "Buyer",
        "job_family": "Supply Chain",
        "salary_band": "P1",
        "base_annual_usd": 58000,
    },
    {
        "role_id": 14,
        "role_name": "Supply Chain Manager",
        "job_family": "Supply Chain",
        "salary_band": "M1",
        "base_annual_usd": 135000,
    },
    {
        "role_id": 15,
        "role_name": "Process Engineer",
        "job_family": "Engineering",
        "salary_band": "E2",
        "base_annual_usd": 95000,
    },
    {
        "role_id": 16,
        "role_name": "Equipment Engineer",
        "job_family": "Engineering",
        "salary_band": "E3",
        "base_annual_usd": 105000,
    },
    {
        "role_id": 17,
        "role_name": "Engineering Manager",
        "job_family": "Engineering",
        "salary_band": "M1",
        "base_annual_usd": 142000,
    },
]

FUNCTION_ROLE_MIX = {
    "Fab Operations": {1: 0.55, 2: 0.20, 3: 0.22, 4: 0.03},
    "Test & Assembly": {5: 0.62, 6: 0.30, 7: 0.08},
    "Facilities": {8: 0.65, 9: 0.35},
    "Quality": {10: 0.55, 11: 0.45},
    "Supply Chain": {12: 0.45, 13: 0.45, 14: 0.10},
    "Engineering": {15: 0.45, 16: 0.42, 17: 0.13},
}

SITE_FUNCTION_MIX = {
    1: {
        "Fab Operations": 0.42,
        "Test & Assembly": 0.20,
        "Facilities": 0.10,
        "Quality": 0.08,
        "Supply Chain": 0.08,
        "Engineering": 0.12,
    },
    2: {
        "Fab Operations": 0.38,
        "Test & Assembly": 0.22,
        "Facilities": 0.11,
        "Quality": 0.09,
        "Supply Chain": 0.08,
        "Engineering": 0.12,
    },
    3: {
        "Fab Operations": 0.45,
        "Test & Assembly": 0.25,
        "Facilities": 0.09,
        "Quality": 0.07,
        "Supply Chain": 0.06,
        "Engineering": 0.08,
    },
    4: {
        "Fab Operations": 0.35,
        "Test & Assembly": 0.20,
        "Facilities": 0.13,
        "Quality": 0.10,
        "Supply Chain": 0.09,
        "Engineering": 0.13,
    },
}

SITE_START_END_HEADCOUNT = {1: (920, 860), 2: (820, 780), 3: (830, 740), 4: (540, 420)}
SITE_SEASONALITY = {1: (0.05, 1.5), 2: (0.05, 2.0), 3: (0.07, 0.5), 4: (0.03, 1.0)}
SITE_BASE_ATTRITION = {1: 0.009, 2: 0.011, 3: 0.014, 4: 0.012}
FAMILY_ATTRITION_MULTIPLIER = {
    "Fab Operations": 1.30,
    "Test & Assembly": 1.15,
    "Facilities": 0.85,
    "Quality": 0.90,
    "Supply Chain": 0.80,
    "Engineering": 1.00,
}
SITE_SALARY_FACTOR = {1: 1.00, 2: 1.00, 3: 0.55, 4: 0.70}
LABOR_LOAD_FACTOR = 1.30
NONLABOR_RATIO = {
    "Fab Operations": 0.60,
    "Test & Assembly": 0.45,
    "Facilities": 1.05,
    "Quality": 0.15,
    "Supply Chain": 0.25,
    "Engineering": 0.12,
}
BASE_OVERTIME_FRACTION = {1: 0.045, 2: 0.05, 3: 0.06, 4: 0.05}

OVERTIME_ANOMALY_SITE_ID = 3
OVERTIME_ANOMALY_MONTHS = {date(2025, 6, 1), date(2025, 7, 1), date(2025, 8, 1), date(2025, 9, 1)}
OVERTIME_ANOMALY_MULTIPLIER = 3.5
NONLABOR_UNDERSPEND_RANGE = (0.48, 0.62)

CC_ID_BY_KEY = {
    (site["site_id"], function): i * len(FUNCTIONS) + j + 1
    for i, site in enumerate(SITES)
    for j, function in enumerate(FUNCTIONS)
}
NONLABOR_UNDERSPEND_CC_ID = CC_ID_BY_KEY[(4, "Facilities")]
ROLES_BY_ID = {role["role_id"]: role for role in ROLES}


def _month_starts() -> list[date]:
    starts = []
    year, month = HISTORY_START.year, HISTORY_START.month
    for _ in range(HISTORY_MONTHS):
        starts.append(date(year, month, 1))
        month += 1
        if month == 13:
            month, year = 1, year + 1
    return starts


def _month_end(month_start: date) -> date:
    year = month_start.year + (month_start.month == 12)
    month = month_start.month % 12 + 1
    return date(year, month, 1) - timedelta(days=1)


def _headcount_target(site_id: int, month_index: int) -> float:
    start, end = SITE_START_END_HEADCOUNT[site_id]
    amplitude, phase = SITE_SEASONALITY[site_id]
    linear = start + (end - start) * month_index / (HISTORY_MONTHS - 1)
    seasonal = 1 + amplitude * math.sin(2 * math.pi * (month_index + phase) / 12.0)
    return max(0.0, linear * seasonal)


def _attrition_rate(rng: random.Random, site_id: int, function: str) -> float:
    base = SITE_BASE_ATTRITION[site_id] * FAMILY_ATTRITION_MULTIPLIER[function]
    rate = base * rng.uniform(0.85, 1.15)
    return min(0.022, max(0.008, rate))


def _weighted_choice(rng: random.Random, weights: dict):
    population = list(weights)
    return rng.choices(population, weights=[weights[key] for key in population])[0]


def _past_hire_date(rng: random.Random) -> date:
    days_ago = 30 + int((rng.random() ** 0.6) * 6790)
    return HISTORY_START - timedelta(days=days_ago)


def _shift(rng: random.Random, function: str) -> str:
    if function in ("Fab Operations", "Test & Assembly"):
        return rng.choices(["A", "B", "C", "Days"], weights=[0.33, 0.33, 0.25, 0.09])[0]
    if function == "Facilities":
        return rng.choices(["Days", "A"], weights=[0.80, 0.20])[0]
    return rng.choices(["Days", "A"], weights=[0.97, 0.03])[0]


def _annual_salary(rng: random.Random, role_id: int, site_id: int) -> float:
    base = ROLES_BY_ID[role_id]["base_annual_usd"]
    factor = SITE_SALARY_FACTOR[site_id] * min(1.25, max(0.85, rng.gauss(1.0, 0.08)))
    return round(base * factor / 50.0) * 50.0


def _make_employee(
    rng: random.Random,
    employee_id: int,
    site_id: int,
    function: str,
    hire_date: date,
    term_date: date | None = None,
) -> dict:
    role_id = _weighted_choice(rng, FUNCTION_ROLE_MIX[function])
    return {
        "employee_id": employee_id,
        "site_id": site_id,
        "cost_center_id": CC_ID_BY_KEY[(site_id, function)],
        "role_id": role_id,
        "shift": _shift(rng, function),
        "hire_date": hire_date,
        "term_date": term_date,
        "fte": rng.choices([1.0, 0.9, 0.8], weights=[0.92, 0.05, 0.03])[0],
        "annual_salary_usd": _annual_salary(rng, role_id, site_id),
    }


def _simulate_roster(rng: random.Random) -> tuple[pd.DataFrame, dict[int, list[int]]]:
    employees = []
    next_employee_id = 10001
    starts = _month_starts()
    active_by_site = {site["site_id"]: [] for site in SITES}
    initial_headcount = {
        site_id: round(start) for site_id, (start, _) in SITE_START_END_HEADCOUNT.items()
    }

    for site in SITES:
        site_id = site["site_id"]
        for _ in range(initial_headcount[site_id]):
            function = _weighted_choice(rng, SITE_FUNCTION_MIX[site_id])
            employee = _make_employee(
                rng, next_employee_id, site_id, function, _past_hire_date(rng)
            )
            employees.append(employee)
            active_by_site[site_id].append(employee)
            next_employee_id += 1

    hires_by_site_month = {site_id: [0] * HISTORY_MONTHS for site_id in initial_headcount}
    for month_index, month_start in enumerate(starts):
        month_end = _month_end(month_start)
        for site in SITES:
            site_id = site["site_id"]
            active = [emp for emp in active_by_site[site_id] if emp["term_date"] is None]
            for employee in active:
                function = ROLES_BY_ID[employee["role_id"]]["job_family"]
                if rng.random() < _attrition_rate(rng, site_id, function):
                    employee["term_date"] = month_end
            remaining = sum(1 for emp in active if emp["term_date"] is None)
            n_hires = max(0, int(round(_headcount_target(site_id, month_index) - remaining)))
            hires_by_site_month[site_id][month_index] = n_hires
            for _ in range(n_hires):
                function = _weighted_choice(rng, SITE_FUNCTION_MIX[site_id])
                hire_date = month_start + timedelta(days=rng.randint(0, 27))
                employee = _make_employee(rng, next_employee_id, site_id, function, hire_date)
                employees.append(employee)
                active_by_site[site_id].append(employee)
                next_employee_id += 1

    return pd.DataFrame(employees), hires_by_site_month


def _build_requisitions(
    rng: random.Random, roster: pd.DataFrame, hires_by_site_month: dict[int, list[int]]
) -> pd.DataFrame:
    requisitions = []
    next_req_id = 5001
    window_hires = roster[roster["hire_date"] >= HISTORY_START]
    for _, employee in window_hires.iterrows():
        fill_lag = rng.randint(30, 90)
        opened = employee["hire_date"] - timedelta(days=fill_lag)
        target_start = opened + timedelta(days=rng.randint(30, 60))
        requisitions.append(
            {
                "req_id": next_req_id,
                "site_id": employee["site_id"],
                "cost_center_id": employee["cost_center_id"],
                "role_id": employee["role_id"],
                "opened_date": opened,
                "target_start_date": target_start,
                "filled_date": employee["hire_date"],
                "status": "filled",
            }
        )
        next_req_id += 1

    starts = _month_starts()
    for site_id, monthly_hires in hires_by_site_month.items():
        for month_index, n_hires in enumerate(monthly_hires):
            if n_hires == 0:
                continue
            n_extra = 1 + int(round(n_hires * 0.08))
            for _ in range(n_extra):
                opened = starts[month_index] + timedelta(days=rng.randint(0, 27))
                if month_index >= HISTORY_MONTHS - 6:
                    status = rng.choices(
                        ["open", "on_hold", "cancelled"], weights=[0.75, 0.15, 0.10]
                    )[0]
                else:
                    status = rng.choices(["cancelled", "on_hold"], weights=[0.60, 0.40])[0]
                function = _weighted_choice(rng, SITE_FUNCTION_MIX[site_id])
                requisitions.append(
                    {
                        "req_id": next_req_id,
                        "site_id": site_id,
                        "cost_center_id": CC_ID_BY_KEY[(site_id, function)],
                        "role_id": _weighted_choice(rng, FUNCTION_ROLE_MIX[function]),
                        "opened_date": opened,
                        "target_start_date": opened + timedelta(days=rng.randint(30, 90)),
                        "filled_date": None,
                        "status": status,
                    }
                )
                next_req_id += 1

    return pd.DataFrame(requisitions)


def _facilities_seasonal(function: str, month_start: date) -> float:
    if function != "Facilities":
        return 1.0
    if month_start.month in (11, 12, 1, 2):
        return 1.15
    if month_start.month in (7, 8):
        return 1.10
    return 1.0


def _build_budget_plan(rng: random.Random, cost_centers: pd.DataFrame) -> pd.DataFrame:
    cc_bias = {
        row["cost_center_id"]: rng.uniform(-0.06, 0.06) for _, row in cost_centers.iterrows()
    }
    avg_annual_by_function = {}
    for function, mix in FUNCTION_ROLE_MIX.items():
        total_weight = sum(mix.values())
        avg_annual_by_function[function] = (
            sum(ROLES_BY_ID[role_id]["base_annual_usd"] * weight for role_id, weight in mix.items())
            / total_weight
        )

    rows = []
    starts = _month_starts()
    for _, cc in cost_centers.iterrows():
        site_id = cc["site_id"]
        function = cc["function"]
        monthly_salary = (
            avg_annual_by_function[function]
            * SITE_SALARY_FACTOR[site_id]
            * LABOR_LOAD_FACTOR
            / 12.0
        )
        for month_index, month_start in enumerate(starts):
            target = _headcount_target(site_id, month_index)
            share = SITE_FUNCTION_MIX[site_id][function]
            planned_headcount = max(
                0.0, round(target * share * (1 + cc_bias[cc["cost_center_id"]]), 1)
            )
            planned_labor = round(planned_headcount * monthly_salary, 2)
            ratio = NONLABOR_RATIO[function] * _facilities_seasonal(function, month_start)
            planned_nonlabor = round(planned_labor * ratio * rng.uniform(0.95, 1.05), 2)
            rows.append(
                {
                    "cost_center_id": cc["cost_center_id"],
                    "fiscal_month": month_start,
                    "planned_headcount": planned_headcount,
                    "planned_labor_usd": planned_labor,
                    "planned_nonlabor_usd": planned_nonlabor,
                }
            )
    return pd.DataFrame(rows)


def _build_actual_spend(
    rng: random.Random,
    roster: pd.DataFrame,
    cost_centers: pd.DataFrame,
    budget: pd.DataFrame,
) -> pd.DataFrame:
    hire_dt = pd.to_datetime(roster["hire_date"])
    term_dt = pd.to_datetime(roster["term_date"])
    monthly = roster["annual_salary_usd"] * roster["fte"] / 12.0
    plan_by_key = {
        (row["cost_center_id"], row["fiscal_month"]): row for row in budget.to_dict("records")
    }

    rows = []
    starts = _month_starts()
    for month_start in starts:
        month_end = _month_end(month_start)
        active = (hire_dt <= pd.Timestamp(month_end)) & (
            term_dt.isna() | (term_dt >= pd.Timestamp(month_start))
        )
        labor_by_cc = monthly.loc[active].groupby(roster.loc[active, "cost_center_id"]).sum()
        for _, cc in cost_centers.iterrows():
            cc_id = cc["cost_center_id"]
            base_labor = float(labor_by_cc.get(cc_id, 0.0))
            overtime_fraction = BASE_OVERTIME_FRACTION[cc["site_id"]] * rng.uniform(0.85, 1.25)
            if month_start in OVERTIME_ANOMALY_MONTHS and cc["site_id"] == OVERTIME_ANOMALY_SITE_ID:
                overtime_fraction *= OVERTIME_ANOMALY_MULTIPLIER
            labor_noise = rng.choice([-1, 1]) * rng.uniform(0.005, 0.02)
            actual_labor = round(base_labor * LABOR_LOAD_FACTOR * (1 + labor_noise), 2)
            actual_overtime = round(base_labor * overtime_fraction, 2)
            planned_nonlabor = float(plan_by_key[(cc_id, month_start)]["planned_nonlabor_usd"])
            if cc_id == NONLABOR_UNDERSPEND_CC_ID:
                actual_nonlabor = round(
                    planned_nonlabor * rng.uniform(*NONLABOR_UNDERSPEND_RANGE), 2
                )
            else:
                deviation = rng.choice([-1, 1]) * rng.uniform(0.03, 0.12)
                actual_nonlabor = round(planned_nonlabor * (1 + deviation), 2)
            rows.append(
                {
                    "cost_center_id": cc_id,
                    "fiscal_month": month_start,
                    "actual_labor_usd": actual_labor,
                    "actual_overtime_usd": actual_overtime,
                    "actual_nonlabor_usd": actual_nonlabor,
                }
            )
    return pd.DataFrame(rows)


def generate_datasets() -> dict[str, pd.DataFrame]:
    random.seed(SEED)
    np.random.seed(SEED)
    rng = random.Random(SEED)

    cost_centers_rows = []
    for i, site in enumerate(SITES):
        for j, function in enumerate(FUNCTIONS):
            cost_centers_rows.append(
                {
                    "cost_center_id": i * len(FUNCTIONS) + j + 1,
                    "site_id": site["site_id"],
                    "cc_code": f"{SITE_CODES[site['site_id']]}-{FUNCTION_CODES[function]}",
                    "cc_name": f"{site['site_name']} {function}",
                    "function": function,
                }
            )
    cost_centers = pd.DataFrame(cost_centers_rows)

    roster, hires_by_site_month = _simulate_roster(rng)
    budget = _build_budget_plan(rng, cost_centers)
    return {
        "sites": pd.DataFrame(SITES),
        "roles": pd.DataFrame(ROLES),
        "cost_centers": cost_centers,
        "headcount_roster": roster,
        "requisitions": _build_requisitions(rng, roster, hires_by_site_month),
        "budget_plan": budget,
        "actual_spend": _build_actual_spend(rng, roster, cost_centers, budget),
    }


def _wait_for_db(engine, retries: int = 60, delay: float = 1.0) -> None:
    last_error = None
    for _ in range(retries):
        try:
            with engine.connect():
                return
        except OperationalError as exc:
            last_error = exc
            time.sleep(delay)
    raise RuntimeError(f"database not reachable after {retries} attempts") from last_error


def main() -> None:
    datasets = generate_datasets()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    for name, df in datasets.items():
        df.to_csv(DATA_DIR / f"{name}.csv", index=False)

    engine = get_engine()
    _wait_for_db(engine)
    run_sql_file(engine, SCHEMA_PATH)
    for name in LOAD_ORDER:
        insert_dataframe(engine, name, datasets[name])

    active_headcount = int(datasets["headcount_roster"]["term_date"].isna().sum())
    print(f"wrote CSVs to {DATA_DIR}")
    print(
        f"seeded PostgreSQL: {len(datasets['headcount_roster'])} roster rows, "
        f"{active_headcount} active employees, {len(datasets['cost_centers'])} cost centers, "
        f"{len(datasets['budget_plan'])} budget/actual months"
    )


if __name__ == "__main__":
    main()
