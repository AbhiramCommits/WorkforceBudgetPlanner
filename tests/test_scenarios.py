import dataclasses

import pandas as pd
import pytest

from wbp.scenarios import (
    CANONICAL_COLUMNS,
    EngineInputs,
    _run_engine,
    accelerated_hiring,
    attrition_spike,
    break_even_analysis,
    break_even_ot_hours,
    compare_scenarios,
    compose,
    hiring_freeze,
    overtime_shift,
)


def _inputs() -> EngineInputs:
    return EngineInputs(
        current_active={1: 100.0, 2: 50.0},
        attrition_rate={1: 0.12, 2: 0.24},
        family_attrition_rate={(1, "Fab Operations"): 0.12},
        family_headcount={(1, "Fab Operations"): 100.0},
        fills={(1, 1): 2.0, (1, 2): 1.0, (2, 1): 1.0},
        blended_salary={1: 60000.0, 2: 72000.0},
        fringe_rate=0.28,
        overtime_fraction=0.05,
        overtime_premium=1.5,
        max_ot_hours_per_head=60,
        horizon=6,
        as_of=pd.Timestamp("2026-08-31"),
    )


def _site_month(frame, site_id, month):
    row = frame[
        (frame["site_id"] == site_id)
        & (frame["fiscal_month"] == frame["fiscal_month"].min() + pd.DateOffset(months=month - 1))
    ]
    return row.iloc[0]


def test_baseline_schema_and_math():
    baseline = _run_engine(_inputs())
    assert list(baseline.columns) == CANONICAL_COLUMNS
    assert len(baseline) == 2 * 6
    row = _site_month(baseline, 1, 1)
    assert row["monthly_attrition"] == 1.0
    assert row["req_fills"] == 2.0
    assert row["headcount"] == 101.0
    assert row["labor_usd"] == round(101 * 60000 / 12 * 1.28, 2)
    assert row["overtime_usd"] == round(row["labor_usd"] * 0.05, 2)
    assert row["total_labor_usd"] == round(row["labor_usd"] + row["overtime_usd"], 2)


def test_hiring_freeze_stops_fills_after_start_month():
    frame = _run_engine(_inputs(), hiring_freeze(start_month=2))
    assert _site_month(frame, 1, 1)["req_fills"] == 2.0
    assert _site_month(frame, 1, 2)["req_fills"] == 1.0
    assert _site_month(frame, 1, 3)["req_fills"] == 0.0


def test_attrition_spike_multiplies_attrition():
    baseline = _run_engine(_inputs())
    spiked = _run_engine(_inputs(), attrition_spike(multiplier=2.0, months=2))
    assert _site_month(spiked, 1, 1)["monthly_attrition"] == 2.0
    assert _site_month(spiked, 1, 2)["monthly_attrition"] == 2.0
    assert _site_month(spiked, 1, 3)["monthly_attrition"] == round(99 * 0.12 / 12, 2)
    assert _site_month(spiked, 2, 1)["monthly_attrition"] == round(
        _site_month(baseline, 2, 1)["monthly_attrition"] * 2, 2
    )


def test_attrition_spike_targets_job_family():
    spiked = _run_engine(
        _inputs(), attrition_spike(multiplier=2.0, months=12, job_family="Fab Operations")
    )
    assert _site_month(spiked, 1, 1)["monthly_attrition"] == 2.0
    assert _site_month(spiked, 2, 1)["monthly_attrition"] == 1.0


def test_overtime_shift_covers_gap_at_premium_with_cap():
    frame = _run_engine(
        _inputs(), overtime_shift(ot_hours_per_head=160, premium=1.5, cap_hours_per_head=60)
    )
    row = _site_month(frame, 1, 1)
    assert row["headcount"] == 99.0
    labor = round(99 * 60000 / 12 * 1.28, 2)
    covered = 2.0 * 160.0
    expected_ot = round(labor * 0.05 + covered * 60000 / 2080 * 1.5, 2)
    assert row["overtime_usd"] == expected_ot


def test_accelerated_hiring_adds_fills_and_ramp_cost():
    frame = _run_engine(
        _inputs(), accelerated_hiring(extra_reqs_per_month=5, ramp_cost_per_hire=5000)
    )
    row = _site_month(frame, 1, 1)
    assert row["req_fills"] == 7.0
    assert row["headcount"] == 106.0
    assert row["other_usd"] == 25000.0


def test_compose_applies_both_scenarios():
    frame = _run_engine(
        _inputs(),
        compose(attrition_spike(multiplier=2.0, months=12), hiring_freeze(start_month=2)),
    )
    assert _site_month(frame, 1, 1)["headcount"] == 100.0
    assert _site_month(frame, 1, 2)["headcount"] == 99.0
    assert _site_month(frame, 1, 3)["headcount"] == round(99 - 1.98, 2)


def test_compare_scenarios_reports_deltas():
    inputs = _inputs()
    baseline = _run_engine(inputs)
    spiked = _run_engine(inputs, attrition_spike(multiplier=2.0, months=12))
    comparison = compare_scenarios(baseline, {"spike": spiked})
    assert len(comparison) == 2
    baseline_row = comparison[comparison["scenario"] == "baseline"].iloc[0]
    spike_row = comparison[comparison["scenario"] == "spike"].iloc[0]
    assert baseline_row["delta_total_usd"] == 0.0
    expected_delta = round(spike_row["total_cost_usd"] - baseline_row["total_cost_usd"], 2)
    assert spike_row["delta_total_usd"] == expected_delta


def test_break_even_analysis_finds_crossover():
    grid, crossover = break_even_analysis(_inputs(), multipliers=[1.0, 1.5, 2.0])
    assert len(grid) == 3
    assert crossover == 1.0
    assert list(grid.columns) == [
        "attrition_multiplier",
        "backfill_cost_usd",
        "overtime_policy_cost_usd",
        "ot_minus_backfill_usd",
    ]


def test_break_even_ot_hours_unit_math():
    hours = break_even_ot_hours(_inputs(), hire_cost=5000)
    blended = (100 * 60000 + 50 * 72000) / 150
    expected = (1.28 / 12 + 5000 / (12 * blended)) * 2080 / 1.5
    assert hours == pytest.approx(expected)


def test_scenario_factories_read_params_defaults():
    assert hiring_freeze().params["start_month"] == 1
    assert attrition_spike().params["multiplier"] == 1.75
    assert attrition_spike().params["months"] == 6
    assert overtime_shift().params["ot_hours_per_head"] == 160
    assert accelerated_hiring().params["extra_reqs_per_month"] == 5
    assert accelerated_hiring().params["ramp_cost_per_hire"] == 5000


def test_family_spike_needs_family_inputs():
    inputs = dataclasses.replace(_inputs(), family_attrition_rate={}, family_headcount={})
    spiked = _run_engine(
        inputs, attrition_spike(multiplier=2.0, months=12, job_family="Fab Operations")
    )
    assert _site_month(spiked, 1, 1)["monthly_attrition"] == 1.0
