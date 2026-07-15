"""SQL adapter: validated filter → parameterized SELECT.

Translates a PlainQuery validated filter into a parameterized SQL query
that a customer can execute against their own database. This is a reference
implementation proving the validated filter can drive a real search backend.

Security model:
- Values are NEVER interpolated into the SQL string. All values go into
  a separate params list, returned alongside the SQL. The caller binds them
  via their database driver's parameterized query support.
- Column and table names are validated against the schema before emission.
  Only known schema fields appear in the SQL. This is defense in depth —
  the validated filter is already schema-clean, but the adapter does not
  trust that blindly.

This module does NOT execute anything. It emits (sql_string, params).
Whether to run it is the customer's concern.
"""

from ..schema import Schema
from ..validator import ValidatedFilter

# Operator mapping: PlainQuery op → SQL operator
_OP_MAP = {
    "eq": "=",
    "lte": "<=",
    "gte": ">=",
}


def to_sql(
    vf: ValidatedFilter, schema: Schema, table_name: str
) -> tuple[str, list]:
    """Convert a validated filter into a parameterized SQL SELECT.

    Args:
        vf: A ValidatedFilter (already schema-validated by validator.py).
        schema: The schema the filter was validated against.
        table_name: The SQL table to query. Must be a simple identifier.

    Returns:
        (sql_string, params) where sql_string uses ? placeholders and
        params is the list of values in placeholder order.
    """
    _validate_identifier(table_name)

    clauses = []
    params = []

    for field_name, constraint in vf.filters.items():
        field_def = schema.fields.get(field_name)
        if field_def is None:
            # Not in schema — skip silently. Should never happen with a
            # properly validated filter, but defense in depth.
            continue

        _validate_identifier(field_name)

        if field_def.type == "string":
            # Free-text substring match — mirrors backend.py's LIKE semantics
            clauses.append(f"{field_name} LIKE ?")
            params.append(f"%{constraint}%")

        elif field_def.type == "enum":
            clauses.append(f"{field_name} = ?")
            params.append(constraint)

        elif field_def.type in ("int", "date"):
            if isinstance(constraint, dict):
                op = constraint.get("op")
                if op == "between":
                    clauses.append(f"{field_name} BETWEEN ? AND ?")
                    params.append(constraint["low"])
                    params.append(constraint["high"])
                elif op in _OP_MAP:
                    clauses.append(f"{field_name} {_OP_MAP[op]} ?")
                    params.append(constraint["value"])

    # Build SELECT
    sql = f"SELECT * FROM {table_name}"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)

    # Sort
    if vf.sort:
        sort_col, sort_dir = _parse_sort(vf.sort)
        if sort_col:
            _validate_identifier(sort_col)
            sql += f" ORDER BY {sort_col} {sort_dir}"

    # Limit
    sql += " LIMIT ?"
    params.append(vf.limit)

    return sql, params


def _parse_sort(sort: str) -> tuple[str, str]:
    """Parse 'field_asc' or 'field_desc' into (field, 'ASC'|'DESC')."""
    parts = sort.rsplit("_", 1)
    if len(parts) != 2 or parts[1] not in ("asc", "desc"):
        return "", ""
    return parts[0], parts[1].upper()


def _validate_identifier(name: str) -> None:
    """Reject anything that isn't a simple SQL identifier.

    Allows only alphanumeric characters and underscores. This prevents
    SQL injection through column or table names — even though the caller
    controls these, defense in depth costs nothing here.
    """
    if not name.isidentifier():
        raise ValueError(
            f"Invalid SQL identifier: {name!r}. "
            f"Only alphanumeric characters and underscores are allowed."
        )
