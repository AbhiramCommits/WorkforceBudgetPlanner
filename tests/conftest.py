from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError

from wbp.db import get_database_url, get_engine, run_sql_file

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = PROJECT_ROOT / "sql" / "schema.sql"


@pytest.fixture(scope="session")
def pg_engine():
    engine = get_engine()
    try:
        with engine.connect():
            pass
    except OperationalError:
        pytest.skip("PostgreSQL not available")
    return engine


@pytest.fixture()
def test_db_engine():
    admin = get_engine()
    try:
        with admin.connect():
            pass
    except OperationalError:
        pytest.skip("PostgreSQL not available")

    url = make_url(get_database_url())
    test_url = url.set(database="wbp_test")
    with admin.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
        conn.execute(text("DROP DATABASE IF EXISTS wbp_test WITH (FORCE)"))
        conn.execute(text("CREATE DATABASE wbp_test"))

    engine = create_engine(test_url.render_as_string(hide_password=False))
    try:
        run_sql_file(engine, SCHEMA_PATH)
        yield engine
    finally:
        engine.dispose()
        with admin.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
            conn.execute(text("DROP DATABASE IF EXISTS wbp_test WITH (FORCE)"))
