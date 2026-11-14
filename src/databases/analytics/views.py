"""Dashboard read models derived from analytics tables.

The views are defined in ``migrations/R__dashboard_views.sql``. Flyway rebuilds
them when that file changes. They are refreshed together, inside one
transaction, at the end of each analytics run. Each materialized view has a
unique index so ``REFRESH ... CONCURRENTLY`` never blocks readers. Plain views
serve paginated drill-down queries from indexed base tables.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection

# Keep the three name lists in sync with migrations/R__dashboard_views.sql;
# tests/test_flyway_migrations.py compares them.
PLAIN_VIEWS = ("current_stances", "attributed_units", "dash_statements")
MATERIALIZED_VIEWS = (
    "dash_representatives",
    "dash_representative_positions",
    "dash_representative_monthly",
    "dash_topics",
    "dash_topic_trends",
    "dash_sources",
)

DASHBOARD_READER_ROLE = "analytics_reader"
# The dashboard role never sees voice centroids, embeddings, or manual notes.
DASHBOARD_OBJECTS = (
    "schema_state",
    "analysis_runs",
    "representatives",
    "policy_topics",
    "dash_statements",
    *MATERIALIZED_VIEWS,
)


def refresh_dashboard_views(connection: Connection) -> None:
    """Refresh every rollup in the caller's transaction so readers see one state."""
    for name in MATERIALIZED_VIEWS:
        connection.execute(text(f"REFRESH MATERIALIZED VIEW CONCURRENTLY {name}"))
    connection.execute(
        text(
            "INSERT INTO schema_state (key, value) "
            "VALUES ('dashboard_refreshed_at', now()::text) "
            "ON CONFLICT (key) DO UPDATE "
            "SET value = excluded.value, updated_at = now()"
        )
    )
