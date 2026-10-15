"""Read a real relational database's tables and columns (read-only).

Needs SQLAlchemy: install the harness with the `relational` extra.
"""


def read_structure(url: str) -> dict[str, set[str]]:
    """Every table in the database, with its column names."""
    try:
        from sqlalchemy import create_engine, inspect
    except ImportError as exc:
        raise RuntimeError(
            "reading the database needs SQLAlchemy; install harness[relational]"
        ) from exc
    engine = create_engine(url)
    try:
        inspector = inspect(engine)
        return {
            table: {column["name"] for column in inspector.get_columns(table)}
            for table in inspector.get_table_names()
        }
    finally:
        engine.dispose()
