import os
from pathlib import Path

import pandas as pd
from dotenv import load_dotenv
from sqlalchemy import MetaData, Table, create_engine, insert, text

DEFAULT_DATABASE_URL = "postgresql+psycopg2://wbp:wbp_password@localhost:5433/workforce_budget"

load_dotenv()


def get_database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def get_engine(database_url: str | None = None, echo: bool = False):
    return create_engine(database_url or get_database_url(), echo=echo)


def run_sql_file(engine, path: str | Path) -> None:
    sql = Path(path).read_text()
    statements = [statement.strip() for statement in sql.split(";") if statement.strip()]
    with engine.begin() as conn:
        for statement in statements:
            conn.execute(text(statement))


def query_df(engine, sql: str, params: dict | None = None) -> pd.DataFrame:
    return pd.read_sql(text(sql), engine, params=params)


def insert_dataframe(engine, table_name: str, df: pd.DataFrame, chunksize: int = 2000) -> None:
    data = df.copy()
    for col in data.columns:
        if pd.api.types.is_datetime64_any_dtype(data[col]):
            data[col] = data[col].dt.date
    data = data.astype(object).where(data.notna(), None)
    table = Table(table_name, MetaData(), autoload_with=engine)
    rows = data.to_dict("records")
    with engine.begin() as conn:
        for start in range(0, len(rows), chunksize):
            conn.execute(insert(table), rows[start : start + chunksize])
