"""Post-seed data quality checks with a pass/fail Markdown report."""

import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from .db import get_engine, query_df
from .generate_data import HISTORY_END, HISTORY_MONTHS, HISTORY_START

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = PROJECT_ROOT / "reports" / "data_quality.md"


@dataclass
class QualityCheck:
    name: str
    passed: bool
    detail: str


def run_quality_checks(engine) -> list[QualityCheck]:
    checks = []

    term_violations = query_df(
        engine,
        "SELECT count(*) AS n FROM headcount_roster "
        "WHERE term_date IS NOT NULL AND term_date < hire_date",
    )["n"].iloc[0]
    checks.append(
        QualityCheck(
            "term_date_not_before_hire_date",
            term_violations == 0,
            f"{term_violations} violating rows",
        )
    )

    unbudgeted = query_df(
        engine,
        "WITH active_cc AS ("
        "  SELECT DISTINCT cost_center_id FROM headcount_roster WHERE term_date IS NULL"
        "),"
        "budgeted AS ("
        "  SELECT cost_center_id,"
        "    COALESCE(SUM(planned_labor_usd + planned_nonlabor_usd), 0) AS planned_dollars"
        "  FROM budget_plan GROUP BY cost_center_id"
        ")"
        "SELECT cc.cost_center_id, cc.cc_name, COALESCE(b.planned_dollars, 0) AS planned_dollars"
        " FROM cost_centers cc"
        " JOIN active_cc a ON a.cost_center_id = cc.cost_center_id"
        " LEFT JOIN budgeted b ON b.cost_center_id = cc.cost_center_id"
        " WHERE COALESCE(b.planned_dollars, 0) <= 0",
    )
    checks.append(
        QualityCheck(
            "active_headcount_has_budget",
            len(unbudgeted) == 0,
            f"{len(unbudgeted)} cost centers with active headcount and no budget",
        )
    )

    duplicates = query_df(
        engine,
        "SELECT"
        "  (SELECT count(*) FROM (SELECT cost_center_id, fiscal_month FROM budget_plan"
        "    GROUP BY 1, 2 HAVING count(*) > 1) t) AS budget_dups,"
        "  (SELECT count(*) FROM (SELECT cost_center_id, fiscal_month FROM actual_spend"
        "    GROUP BY 1, 2 HAVING count(*) > 1) t) AS actual_dups",
    ).iloc[0]
    dup_ok = duplicates["budget_dups"] == 0 and duplicates["actual_dups"] == 0
    checks.append(
        QualityCheck(
            "no_duplicate_cost_center_months",
            dup_ok,
            f"budget_plan: {duplicates['budget_dups']}, actual_spend: {duplicates['actual_dups']}",
        )
    )

    negative_rows = query_df(
        engine,
        "SELECT"
        "  (SELECT count(*) FROM actual_spend WHERE actual_labor_usd < 0"
        "    OR actual_overtime_usd < 0 OR actual_nonlabor_usd < 0)"
        "  + (SELECT count(*) FROM budget_plan WHERE planned_headcount < 0"
        "    OR planned_labor_usd < 0 OR planned_nonlabor_usd < 0) AS n",
    )["n"].iloc[0]
    checks.append(
        QualityCheck("no_negative_spend", negative_rows == 0, f"{negative_rows} negative rows")
    )

    months = query_df(
        engine,
        "SELECT count(DISTINCT fiscal_month) AS n, min(fiscal_month) AS lo, max(fiscal_month) AS hi"
        " FROM budget_plan",
    ).iloc[0]
    cc_months = query_df(
        engine,
        "SELECT cost_center_id, count(DISTINCT fiscal_month) AS n"
        " FROM budget_plan GROUP BY cost_center_id",
    )
    incomplete = int((cc_months["n"] < HISTORY_MONTHS).sum())
    first = pd.to_datetime(months["lo"]).date() if months["lo"] is not None else None
    last = pd.to_datetime(months["hi"]).date() if months["hi"] is not None else None
    expected_last = pd.Timestamp(HISTORY_END).replace(day=1).date()
    horizon_ok = (
        months["n"] == HISTORY_MONTHS
        and first == HISTORY_START
        and last == expected_last
        and incomplete == 0
    )
    checks.append(
        QualityCheck(
            "plan_covers_history_horizon",
            horizon_ok,
            (
                f"{months['n']} distinct months ({first}..{last}), "
                f"{incomplete} incomplete cost centers"
            ),
        )
    )

    return checks


def write_report(checks: list[QualityCheck], path: Path | None = None) -> Path:
    path = path or REPORT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Data Quality Report",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        "",
        "| Check | Status | Detail |",
        "|---|---|---|",
    ]
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        lines.append(f"| {check.name} | {status} | {check.detail} |")
    passed = sum(check.passed for check in checks)
    overall = "PASS" if passed == len(checks) else "FAIL"
    lines += ["", f"**Overall: {overall} ({passed}/{len(checks)} checks passed)**", ""]
    path.write_text("\n".join(lines))
    return path


def main() -> None:
    engine = get_engine()
    checks = run_quality_checks(engine)
    report = write_report(checks)
    for check in checks:
        status = "PASS" if check.passed else "FAIL"
        print(f"{status}  {check.name}: {check.detail}")
    failed = [check for check in checks if not check.passed]
    print(
        f"\noverall: {'PASS' if not failed else 'FAIL'} ({len(checks) - len(failed)}/{len(checks)})"
    )
    print(f"report written to {report}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
