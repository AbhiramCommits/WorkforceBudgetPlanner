import pandas as pd
from sqlalchemy import create_engine, text

from wbp.db import get_engine, insert_dataframe, query_df, run_sql_file


def test_run_sql_file_and_query_df(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    schema = tmp_path / "schema.sql"
    schema.write_text(
        "CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT NOT NULL);\n"
        "INSERT INTO widgets (id, name) VALUES (1, 'alpha');\n"
        "INSERT INTO widgets (id, name) VALUES (2, 'beta');\n"
    )
    run_sql_file(engine, schema)
    df = query_df(engine, "SELECT id, name FROM widgets ORDER BY id")
    assert df.to_dict("records") == [{"id": 1, "name": "alpha"}, {"id": 2, "name": "beta"}]


def test_insert_dataframe_converts_nan_to_null(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    with engine.begin() as conn:
        conn.execute(
            text("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT NOT NULL, nick TEXT)")
        )

    df = pd.DataFrame({"id": [1, 2], "name": ["ada", "grace"], "nick": ["duchess", None]})
    insert_dataframe(engine, "people", df)
    out = query_df(engine, "SELECT * FROM people ORDER BY id")
    assert out["nick"].tolist() == ["duchess", None]


def test_get_engine_with_explicit_url():
    engine = get_engine("sqlite://")
    assert engine.url.drivername == "sqlite"
