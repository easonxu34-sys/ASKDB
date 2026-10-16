from __future__ import annotations

from integrations.database import PostgresConnection


def has_source_grant(connection: PostgresConnection, user_id: str, source_id: str) -> bool:
    """Check direct and role grants inside the caller's transaction.

    Account and source availability remain the caller's responsibility.
    """
    return connection.execute(
        """SELECT 1 WHERE EXISTS (
             SELECT 1 FROM auth_user_data_sources
             WHERE user_id=%s AND data_source_id=%s
           ) OR EXISTS (
             SELECT 1 FROM auth_user_role_bindings AS binding
             JOIN auth_role_data_sources AS grant_row ON grant_row.role_id=binding.role_id
             WHERE binding.user_id=%s AND grant_row.data_source_id=%s
           )""",
        (user_id, source_id, user_id, source_id),
    ).fetchone() is not None


def source_ids_for_user(connection: PostgresConnection, user_id: str) -> set[str]:
    """Return the union of direct and role grants, regardless of source status."""
    rows = connection.execute(
        """SELECT data_source_id FROM auth_user_data_sources WHERE user_id=%s
           UNION
           SELECT grant_row.data_source_id
           FROM auth_user_role_bindings AS binding
           JOIN auth_role_data_sources AS grant_row ON grant_row.role_id=binding.role_id
           WHERE binding.user_id=%s""",
        (user_id, user_id),
    ).fetchall()
    return {row["data_source_id"] for row in rows}
