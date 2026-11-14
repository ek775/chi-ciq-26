"""Dashboard read models derived from analytics tables.

Materialized views are rebuilt only when ``DASHBOARD_VIEWS_VERSION`` changes and
refreshed together, inside one transaction, at the end of each analytics run.
Each has a unique index so ``REFRESH ... CONCURRENTLY`` never blocks readers.
Plain views serve paginated drill-down queries from indexed base tables.
"""

from sqlalchemy import text
from sqlalchemy.engine import Connection

from .models import EMOTIONS

DASHBOARD_VIEWS_VERSION = "1"

_EMOTION_AVERAGES = ", ".join(f"avg(e.{name}) AS {name}" for name in EMOTIONS)
_EMOTION_COLUMNS = ", ".join(f"a.{name}" for name in EMOTIONS)
_DOMINANT_EMOTION = (
    "(SELECT v.k FROM (VALUES "
    + ", ".join(f"('{name}', a.{name})" for name in EMOTIONS)
    + ") AS v(k, x) WHERE v.x IS NOT NULL ORDER BY v.x DESC, v.k LIMIT 1)"
)
_STANCE_COUNTS = ", ".join(
    f"count(*) FILTER (WHERE cs.stance = '{stance}') AS {stance}"
    for stance in ("support", "oppose", "mixed", "neutral")
)
_STANCE_SCORE = (
    "sum(cs.score * cs.confidence) / nullif(sum(cs.confidence), 0) AS stance_score"
)

PLAIN_VIEWS: dict[str, str] = {
    "current_stances": """
        SELECT st.unit_id, st.topic_key, st.stance, st.score, st.confidence,
               st.summary, st.evidence, st.evidence_verified, st.model,
               st.analyzed_at
        FROM stance_results st
        JOIN policy_topics t
          ON t.key = st.topic_key
         AND t.active
         AND t.definition_sha256 = st.definition_sha256
        WHERE st.stance <> 'not_addressed'
    """,
    "attributed_units": """
        SELECT u.id AS unit_id, u.source_key, u.speaker_id, a.representative_id,
               a.method AS attribution_method,
               a.confidence AS attribution_confidence,
               coalesce(u.end_seconds - u.start_seconds, 0) AS seconds
        FROM analysis_units u
        JOIN speaker_attributions a ON a.speaker_id = u.speaker_id
    """,
    "dash_statements": """
        SELECT cs.unit_id, cs.topic_key, cs.stance, cs.score, cs.confidence,
               cs.summary, cs.evidence, cs.evidence_verified, cs.model,
               u.text, u.start_seconds, u.end_seconds, u.source_key,
               src.kind, src.title, src.body, src.meeting_date,
               sp.label AS speaker_label, a.representative_id,
               a.method AS attribution_method,
               a.confidence AS attribution_confidence,
               s.score AS sentiment, e.label AS emotion
        FROM current_stances cs
        JOIN analysis_units u ON u.id = cs.unit_id
        JOIN sources src ON src.key = u.source_key
        LEFT JOIN speakers sp ON sp.id = u.speaker_id
        LEFT JOIN speaker_attributions a ON a.speaker_id = u.speaker_id
        LEFT JOIN sentiment_results s ON s.unit_id = u.id
        LEFT JOIN emotion_results e ON e.unit_id = u.id
    """,
}

MATERIALIZED_VIEWS: dict[str, tuple[str, tuple[str, ...]]] = {
    "dash_representatives": (
        f"""
        WITH activity AS (
            SELECT au.representative_id,
                   count(DISTINCT au.source_key) AS sources,
                   count(*) AS statements,
                   count(*) FILTER (WHERE au.attribution_method = 'manual')
                       AS verified_statements,
                   sum(au.seconds) AS speaking_seconds,
                   avg(s.score) AS mean_sentiment,
                   {_EMOTION_AVERAGES},
                   min(src.meeting_date) AS first_seen,
                   max(src.meeting_date) AS last_seen
            FROM attributed_units au
            JOIN sources src ON src.key = au.source_key
            LEFT JOIN sentiment_results s ON s.unit_id = au.unit_id
            LEFT JOIN emotion_results e ON e.unit_id = au.unit_id
            GROUP BY au.representative_id
        ), positions AS (
            SELECT au.representative_id, count(*) AS positions,
                   count(DISTINCT cs.topic_key) AS topics
            FROM current_stances cs
            JOIN attributed_units au ON au.unit_id = cs.unit_id
            GROUP BY au.representative_id
        ), mentions AS (
            SELECT representative_id, sum(mention_count) AS mentions
            FROM representative_mentions
            GROUP BY representative_id
        )
        SELECT r.id AS representative_id, r.display_name, r.role, r.ward,
               coalesce(a.sources, 0) AS sources,
               coalesce(a.statements, 0) AS statements,
               coalesce(a.verified_statements, 0) AS verified_statements,
               coalesce(a.speaking_seconds, 0) AS speaking_seconds,
               a.mean_sentiment, {_EMOTION_COLUMNS},
               {_DOMINANT_EMOTION} AS dominant_emotion,
               coalesce(p.positions, 0) AS positions,
               coalesce(p.topics, 0) AS topics,
               coalesce(m.mentions, 0) AS mentions,
               a.first_seen, a.last_seen
        FROM representatives r
        LEFT JOIN activity a ON a.representative_id = r.id
        LEFT JOIN positions p ON p.representative_id = r.id
        LEFT JOIN mentions m ON m.representative_id = r.id
        """,
        ("representative_id",),
    ),
    "dash_representative_positions": (
        f"""
        SELECT au.representative_id, cs.topic_key,
               count(*) AS statements,
               count(DISTINCT au.source_key) AS sources,
               {_STANCE_COUNTS},
               count(*) FILTER (WHERE cs.evidence_verified) AS verified_evidence,
               count(*) FILTER (WHERE au.attribution_method = 'manual')
                   AS verified_speaker,
               {_STANCE_SCORE},
               avg(cs.confidence) AS mean_confidence,
               avg(s.score) AS mean_sentiment,
               min(src.meeting_date) AS first_date,
               max(src.meeting_date) AS last_date
        FROM current_stances cs
        JOIN attributed_units au ON au.unit_id = cs.unit_id
        JOIN sources src ON src.key = au.source_key
        LEFT JOIN sentiment_results s ON s.unit_id = cs.unit_id
        GROUP BY au.representative_id, cs.topic_key
        """,
        ("representative_id", "topic_key"),
    ),
    "dash_representative_monthly": (
        f"""
        SELECT au.representative_id,
               date_trunc('month', src.meeting_date)::date AS month,
               count(*) AS statements,
               sum(au.seconds) AS speaking_seconds,
               avg(s.score) AS mean_sentiment,
               {_EMOTION_AVERAGES}
        FROM attributed_units au
        JOIN sources src ON src.key = au.source_key
        LEFT JOIN sentiment_results s ON s.unit_id = au.unit_id
        LEFT JOIN emotion_results e ON e.unit_id = au.unit_id
        WHERE src.meeting_date IS NOT NULL
        GROUP BY au.representative_id, date_trunc('month', src.meeting_date)
        """,
        ("representative_id", "month"),
    ),
    "dash_topics": (
        f"""
        WITH stances AS (
            SELECT cs.topic_key, count(*) AS statements,
                   count(DISTINCT u.source_key) AS sources,
                   {_STANCE_COUNTS}, {_STANCE_SCORE},
                   avg(s.score) AS mean_sentiment,
                   max(src.meeting_date) AS last_date
            FROM current_stances cs
            JOIN analysis_units u ON u.id = cs.unit_id
            JOIN sources src ON src.key = u.source_key
            LEFT JOIN sentiment_results s ON s.unit_id = cs.unit_id
            GROUP BY cs.topic_key
        ), speakers AS (
            SELECT cs.topic_key,
                   count(DISTINCT au.representative_id) AS representatives
            FROM current_stances cs
            JOIN attributed_units au ON au.unit_id = cs.unit_id
            GROUP BY cs.topic_key
        ), relevance AS (
            SELECT topic_key, count(*) AS relevant_units
            FROM topic_relevance
            GROUP BY topic_key
        )
        SELECT p.key AS topic_key, p.area, p.label, p.proposition,
               coalesce(rel.relevant_units, 0) AS relevant_units,
               coalesce(st.statements, 0) AS statements,
               coalesce(st.sources, 0) AS sources,
               coalesce(sp.representatives, 0) AS representatives,
               coalesce(st.support, 0) AS support,
               coalesce(st.oppose, 0) AS oppose,
               coalesce(st.mixed, 0) AS mixed,
               coalesce(st.neutral, 0) AS neutral,
               st.stance_score, st.mean_sentiment, st.last_date
        FROM policy_topics p
        LEFT JOIN stances st ON st.topic_key = p.key
        LEFT JOIN speakers sp ON sp.topic_key = p.key
        LEFT JOIN relevance rel ON rel.topic_key = p.key
        WHERE p.active
        """,
        ("topic_key",),
    ),
    "dash_topic_trends": (
        f"""
        SELECT cs.topic_key,
               date_trunc('month', src.meeting_date)::date AS month,
               src.kind, src.body,
               count(*) AS statements,
               count(DISTINCT src.key) AS sources,
               {_STANCE_COUNTS}, {_STANCE_SCORE},
               avg(s.score) AS mean_sentiment
        FROM current_stances cs
        JOIN analysis_units u ON u.id = cs.unit_id
        JOIN sources src ON src.key = u.source_key
        LEFT JOIN sentiment_results s ON s.unit_id = cs.unit_id
        WHERE src.meeting_date IS NOT NULL
        GROUP BY cs.topic_key, date_trunc('month', src.meeting_date),
                 src.kind, src.body
        """,
        ("topic_key", "month", "kind", "body"),
    ),
    "dash_sources": (
        f"""
        WITH units AS (
            SELECT u.source_key, count(*) AS units,
                   count(a.speaker_id) AS attributed_units,
                   avg(s.score) AS mean_sentiment,
                   {_EMOTION_AVERAGES}
            FROM analysis_units u
            LEFT JOIN speaker_attributions a ON a.speaker_id = u.speaker_id
            LEFT JOIN sentiment_results s ON s.unit_id = u.id
            LEFT JOIN emotion_results e ON e.unit_id = u.id
            GROUP BY u.source_key
        ), voices AS (
            SELECT sp.source_key, count(*) AS speakers,
                   count(a.speaker_id) AS attributed_speakers
            FROM speakers sp
            LEFT JOIN speaker_attributions a ON a.speaker_id = sp.id
            GROUP BY sp.source_key
        ), topics AS (
            SELECT source_key,
                   (array_agg(topic_key ORDER BY n DESC, topic_key))[1:5]
                       AS top_topics
            FROM (
                SELECT u.source_key, cs.topic_key, count(*) AS n
                FROM current_stances cs
                JOIN analysis_units u ON u.id = cs.unit_id
                GROUP BY u.source_key, cs.topic_key
            ) counted
            GROUP BY source_key
        )
        SELECT src.key AS source_key, src.kind, src.title, src.body,
               src.meeting_date, src.duration_seconds,
               coalesce(a.units, 0) AS units,
               coalesce(a.attributed_units, 0) AS attributed_units,
               coalesce(v.speakers, 0) AS speakers,
               coalesce(v.attributed_speakers, 0) AS attributed_speakers,
               a.mean_sentiment, {_EMOTION_COLUMNS},
               {_DOMINANT_EMOTION} AS dominant_emotion,
               coalesce(t.top_topics, ARRAY[]::varchar[]) AS top_topics
        FROM sources src
        LEFT JOIN units a ON a.source_key = src.key
        LEFT JOIN voices v ON v.source_key = src.key
        LEFT JOIN topics t ON t.source_key = src.key
        """,
        ("source_key",),
    ),
}


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


def grant_dashboard_access(connection: Connection, role: str) -> bool:
    """Grant read access to dashboard objects if ``role`` exists."""
    exists = connection.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": role}
    ).scalar()
    if not exists:
        return False
    quoted = connection.dialect.identifier_preparer.quote(role)
    connection.execute(
        text(f"GRANT SELECT ON {', '.join(DASHBOARD_OBJECTS)} TO {quoted}")
    )
    return True


def drop_dashboard_views(connection: Connection) -> None:
    for name in MATERIALIZED_VIEWS:
        connection.execute(text(f"DROP MATERIALIZED VIEW IF EXISTS {name} CASCADE"))
    for name in reversed(PLAIN_VIEWS):
        connection.execute(text(f"DROP VIEW IF EXISTS {name} CASCADE"))


def create_dashboard_views(connection: Connection) -> None:
    for name, query in PLAIN_VIEWS.items():
        connection.execute(text(f"CREATE VIEW {name} AS {query}"))
    for name, (query, unique) in MATERIALIZED_VIEWS.items():
        connection.execute(text(f"CREATE MATERIALIZED VIEW {name} AS {query}"))
        connection.execute(
            text(f"CREATE UNIQUE INDEX {name}_key ON {name} ({', '.join(unique)})")
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
