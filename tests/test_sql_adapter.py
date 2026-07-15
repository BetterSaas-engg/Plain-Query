"""Deterministic tests for the SQL adapter. No LLM calls, no external DB."""

import json
import sqlite3

import pytest

from src.plainquery.adapters.sql import to_sql
from src.plainquery.backend import search
from src.plainquery.schema import load_schema
from src.plainquery.validator import ValidatedFilter


@pytest.fixture
def schema():
    return load_schema("schemas/cars.json")


def _vf(filters=None, sort="price_asc", limit=25):
    return ValidatedFilter(
        filters=filters or {},
        sort=sort,
        limit=limit,
        unmapped=[],
        notes=[],
    )


# === Operator coverage ===


def test_enum_eq(schema):
    vf = _vf(filters={"make": "Honda"})
    sql, params = to_sql(vf, schema, "cars")
    assert "make = ?" in sql
    assert "Honda" in params


def test_string_like(schema):
    vf = _vf(filters={"model": "Civic"})
    sql, params = to_sql(vf, schema, "cars")
    assert "model LIKE ?" in sql
    assert "%Civic%" in params


def test_int_lte(schema):
    vf = _vf(filters={"price": {"op": "lte", "value": 25000}})
    sql, params = to_sql(vf, schema, "cars")
    assert "price <= ?" in sql
    assert 25000 in params


def test_int_gte(schema):
    vf = _vf(filters={"year": {"op": "gte", "value": 2020}})
    sql, params = to_sql(vf, schema, "cars")
    assert "year >= ?" in sql
    assert 2020 in params


def test_int_eq(schema):
    vf = _vf(filters={"year": {"op": "eq", "value": 2022}})
    sql, params = to_sql(vf, schema, "cars")
    assert "year = ?" in sql
    assert 2022 in params


def test_int_between(schema):
    vf = _vf(filters={"year": {"op": "between", "low": 2018, "high": 2022}})
    sql, params = to_sql(vf, schema, "cars")
    assert "year BETWEEN ? AND ?" in sql
    assert 2018 in params
    assert 2022 in params


def test_sort_asc(schema):
    vf = _vf(sort="mileage_asc")
    sql, _ = to_sql(vf, schema, "cars")
    assert "ORDER BY mileage ASC" in sql


def test_sort_desc(schema):
    vf = _vf(sort="year_desc")
    sql, _ = to_sql(vf, schema, "cars")
    assert "ORDER BY year DESC" in sql


def test_limit(schema):
    vf = _vf(limit=10)
    sql, params = to_sql(vf, schema, "cars")
    assert "LIMIT ?" in sql
    assert params[-1] == 10


# === Flagship query ===


def test_flagship_honda_civic_red_under_25k(schema):
    """The demo's signature query: red Honda Civic under 25k, sorted by mileage."""
    vf = _vf(
        filters={
            "make": "Honda",
            "model": "Civic",
            "color": "red",
            "price": {"op": "lte", "value": 25000},
        },
        sort="mileage_asc",
        limit=25,
    )
    sql, params = to_sql(vf, schema, "cars")

    assert sql == (
        "SELECT * FROM cars"
        " WHERE make = ? AND model LIKE ? AND color = ? AND price <= ?"
        " ORDER BY mileage ASC"
        " LIMIT ?"
    )
    assert params == ["Honda", "%Civic%", "red", 25000, 25]


# === Security: no values in SQL string ===


def test_no_values_in_sql_string(schema):
    """Assert that no filter value ever appears in the SQL string itself."""
    vf = _vf(
        filters={
            "make": "Honda",
            "model": "Civic",
            "color": "red",
            "price": {"op": "lte", "value": 25000},
            "year": {"op": "between", "low": 2018, "high": 2024},
        },
        sort="mileage_asc",
        limit=25,
    )
    sql, params = to_sql(vf, schema, "cars")

    # Every param value must NOT appear in the SQL string
    for p in params:
        assert str(p) not in sql, f"Value {p!r} found in SQL string: {sql}"


def test_empty_filter_produces_no_where(schema):
    vf = _vf(filters={})
    sql, params = to_sql(vf, schema, "cars")
    assert "WHERE" not in sql
    assert "LIMIT ?" in sql


def test_invalid_table_name_rejected(schema):
    vf = _vf()
    with pytest.raises(ValueError, match="Invalid SQL identifier"):
        to_sql(vf, schema, "Robert'; DROP TABLE cars;--")


# === Equivalence: SQL adapter agrees with backend.py ===


def test_equivalence_with_backend(schema):
    """Load cars data into SQLite, run the same filter through both backend.py
    and the SQL adapter, and assert the result sets match."""
    data = json.loads(open("data/cars.json", encoding="utf-8").read())

    # Build the validated filter
    vf = _vf(
        filters={
            "make": "Honda",
            "model": "Civic",
            "color": "red",
            "price": {"op": "lte", "value": 25000},
        },
        sort="mileage_asc",
        limit=25,
    )

    # --- backend.py result ---
    backend_rows = search(data, vf, schema)

    # --- SQL adapter result via SQLite ---
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row

    # Create table from first row's keys
    columns = list(data[0].keys())
    col_defs = ", ".join(f"{c} TEXT" for c in columns)
    conn.execute(f"CREATE TABLE cars ({col_defs})")

    # Insert all rows
    placeholders = ", ".join("?" for _ in columns)
    for row in data:
        conn.execute(
            f"INSERT INTO cars ({', '.join(columns)}) VALUES ({placeholders})",
            [str(row.get(c, "")) for c in columns],
        )
    conn.commit()

    # Generate and execute the SQL
    sql, params = to_sql(vf, schema, "cars")

    # SQLite needs CAST for numeric comparisons on TEXT columns
    # Instead, let's use a numeric-typed table for fair comparison
    conn.execute("DROP TABLE cars")
    col_defs_typed = []
    for c in columns:
        if c in ("year", "price", "mileage", "id"):
            col_defs_typed.append(f"{c} INTEGER")
        else:
            col_defs_typed.append(f"{c} TEXT")
    conn.execute(f"CREATE TABLE cars ({', '.join(col_defs_typed)})")

    for row in data:
        vals = []
        for c in columns:
            v = row.get(c, "")
            if c in ("year", "price", "mileage", "id"):
                vals.append(int(v) if v != "" else None)
            else:
                vals.append(str(v))
        conn.execute(
            f"INSERT INTO cars ({', '.join(columns)}) VALUES ({placeholders})",
            vals,
        )
    conn.commit()

    cursor = conn.execute(sql, params)
    sql_rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    # Compare: same IDs in same order
    backend_ids = [r["id"] for r in backend_rows]
    sql_ids = [r["id"] for r in sql_rows]

    assert len(sql_ids) == len(backend_ids), (
        f"Row count mismatch: backend={len(backend_ids)}, sql={len(sql_ids)}"
    )
    assert sql_ids == backend_ids, (
        f"Row order mismatch:\n  backend: {backend_ids}\n  sql:     {sql_ids}"
    )
