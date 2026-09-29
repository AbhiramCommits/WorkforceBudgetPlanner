"""Data quality check tests against transient and live PostgreSQL databases."""

from sqlalchemy import text

from wbp.quality import QualityCheck, run_quality_checks, write_report


def _insert_violating_roster(engine):
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO sites VALUES (1, 'Alpha', 'Americas', 'US')"))
        conn.execute(
            text("INSERT INTO roles VALUES (1, 'Fab Operator', 'Fab Operations', 'OP1', 42000)")
        )
        conn.execute(
            text("INSERT INTO cost_centers VALUES (1, 1, 'ALP-FAB', 'Alpha Fab', 'Fab Operations')")
        )
        conn.execute(
            text(
                "INSERT INTO headcount_roster VALUES "
                "(1, 1, 1, 1, 'A', '2023-01-01', '2022-01-01', 1.0, 42000)"
            )
        )


def test_quality_detects_term_before_hire(test_db_engine):
    _insert_violating_roster(test_db_engine)
    checks = {check.name: check for check in run_quality_checks(test_db_engine)}
    assert not checks["term_date_not_before_hire_date"].passed
    assert "1" in checks["term_date_not_before_hire_date"].detail


def test_quality_detects_negative_spend(test_db_engine):
    with test_db_engine.begin() as conn:
        conn.execute(text("INSERT INTO sites VALUES (1, 'Alpha', 'Americas', 'US')"))
        conn.execute(
            text("INSERT INTO cost_centers VALUES (1, 1, 'ALP-FAB', 'Alpha Fab', 'Fab Operations')")
        )
        conn.execute(text("INSERT INTO actual_spend VALUES (1, '2023-09-01', -100, 0, 0)"))
    checks = {check.name: check for check in run_quality_checks(test_db_engine)}
    assert not checks["no_negative_spend"].passed


def test_quality_detects_unbudgeted_cost_center(test_db_engine):
    with test_db_engine.begin() as conn:
        conn.execute(text("INSERT INTO sites VALUES (1, 'Alpha', 'Americas', 'US')"))
        conn.execute(
            text("INSERT INTO roles VALUES (1, 'Fab Operator', 'Fab Operations', 'OP1', 42000)")
        )
        conn.execute(
            text("INSERT INTO cost_centers VALUES (1, 1, 'ALP-FAB', 'Alpha Fab', 'Fab Operations')")
        )
        conn.execute(
            text(
                "INSERT INTO headcount_roster VALUES "
                "(1, 1, 1, 1, 'A', '2023-01-01', NULL, 1.0, 42000)"
            )
        )
    checks = {check.name: check for check in run_quality_checks(test_db_engine)}
    assert not checks["active_headcount_has_budget"].passed


def test_quality_detects_incomplete_horizon(test_db_engine):
    with test_db_engine.begin() as conn:
        conn.execute(text("INSERT INTO sites VALUES (1, 'Alpha', 'Americas', 'US')"))
        conn.execute(
            text("INSERT INTO cost_centers VALUES (1, 1, 'ALP-FAB', 'Alpha Fab', 'Fab Operations')")
        )
        conn.execute(
            text(
                "INSERT INTO budget_plan VALUES (1, '2023-09-01', 1.0, 100.0, 10.0),"
                " (1, '2023-10-01', 1.0, 100.0, 10.0)"
            )
        )
    checks = {check.name: check for check in run_quality_checks(test_db_engine)}
    assert not checks["plan_covers_history_horizon"].passed


def test_quality_checks_pass_on_seeded_data(pg_engine):
    checks = run_quality_checks(pg_engine)
    assert all(check.passed for check in checks)
    assert {check.name for check in checks} == {
        "term_date_not_before_hire_date",
        "active_headcount_has_budget",
        "no_duplicate_cost_center_months",
        "no_negative_spend",
        "plan_covers_history_horizon",
    }


def test_write_report_emits_markdown(tmp_path):
    checks = [
        QualityCheck("term_date_not_before_hire_date", True, "0 violating rows"),
        QualityCheck("no_negative_spend", False, "2 negative rows"),
    ]
    path = write_report(checks, tmp_path / "data_quality.md")
    content = path.read_text()
    assert "| term_date_not_before_hire_date | PASS | 0 violating rows |" in content
    assert "| no_negative_spend | FAIL | 2 negative rows |" in content
    assert "**Overall: FAIL (1/2 checks passed)**" in content
