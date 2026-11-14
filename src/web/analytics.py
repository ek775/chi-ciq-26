"""Read-only dashboard API over the analytics database.

Queries touch only the dashboard objects granted to ``analytics_reader``.
Filters are bound parameters; sort orders come from fixed allowlists.
"""

import logging
from collections.abc import Iterator
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)

Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]
SLUG = r"^[a-z0-9][a-z0-9-]*$"
Slug = Annotated[str, Query(max_length=100, pattern=SLUG)]
SlugPath = Annotated[str, Path(max_length=100, pattern=SLUG)]
Kind = Literal["meeting", "document"]
Stance = Literal["support", "oppose", "mixed", "neutral"]
Method = Literal["manual", "voice"]
Role = Literal[
    "alderperson", "mayor", "clerk", "treasurer", "official", "staff", "public", "other"
]

REPRESENTATIVE_ORDER = {
    "name": "display_name, representative_id",
    "ward": "ward NULLS LAST, display_name, representative_id",
    "statements": "statements DESC, display_name, representative_id",
    "positions": "positions DESC, display_name, representative_id",
    "sentiment": "mean_sentiment DESC NULLS LAST, representative_id",
    "mentions": "mentions DESC, display_name, representative_id",
}
TOPIC_ORDER = {
    "statements": "statements DESC, topic_key",
    "relevance": "relevant_units DESC, topic_key",
    "support": "stance_score DESC NULLS LAST, topic_key",
    "opposition": "stance_score ASC NULLS LAST, topic_key",
    "area": "area, label, topic_key",
}


def _connection(request: Request) -> Iterator[Connection]:
    engine = getattr(request.app.state, "analytics", None)
    if engine is None:
        raise HTTPException(503, "Analytics database is not configured")
    with engine.connect() as connection, connection.begin():
        connection.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
        )
        yield connection


Database = Annotated[Connection, Depends(_connection)]


def _rows(connection: Connection, query: str, **parameters) -> list[dict]:
    return [dict(row) for row in connection.execute(text(query), parameters).mappings()]


def _where(conditions: list[str]) -> str:
    return f"WHERE {' AND '.join(conditions)}" if conditions else ""


def analytics_router() -> APIRouter:
    router = APIRouter(prefix="/api/analytics", tags=["analytics"])

    @router.get("/summary")
    def summary(database: Database):
        counts = _rows(
            database,
            """
            SELECT
              (SELECT count(*) FROM dash_representatives) AS representatives,
              (SELECT count(*) FROM dash_representatives WHERE statements > 0)
                AS attributed_representatives,
              (SELECT count(*) FROM dash_topics) AS topics,
              (SELECT count(*) FROM dash_sources) AS sources,
              (SELECT count(*) FROM dash_sources WHERE kind = 'meeting')
                AS meetings,
              (SELECT count(*) FROM dash_sources WHERE kind = 'document')
                AS documents,
              (SELECT coalesce(sum(statements), 0) FROM dash_topics)
                AS positions
            """,
        )[0]
        refreshed_at = database.execute(
            text("SELECT value FROM schema_state WHERE key = 'dashboard_refreshed_at'")
        ).scalar()
        last_run = _rows(
            database,
            "SELECT status, started_at, finished_at FROM analysis_runs "
            "ORDER BY started_at DESC LIMIT 1",
        )
        return counts | {
            "refreshed_at": refreshed_at,
            "last_run": last_run[0] if last_run else None,
        }

    @router.get("/representatives")
    def representatives(
        database: Database,
        role: Role | None = None,
        q: Annotated[str, Query(max_length=200)] = "",
        active: bool = False,
        sort: Literal[tuple(REPRESENTATIVE_ORDER)] = "name",
        limit: Limit = 100,
        offset: Offset = 0,
    ):
        conditions, parameters = [], {"limit": limit, "offset": offset}
        if role:
            conditions.append("role = :role")
            parameters["role"] = role
        if q.strip():
            conditions.append("display_name ILIKE :q ESCAPE '\\'")
            parameters["q"] = "%" + _escape_like(q.strip()) + "%"
        if active:
            conditions.append("statements > 0")
        return _rows(
            database,
            f"SELECT * FROM dash_representatives {_where(conditions)} "
            f"ORDER BY {REPRESENTATIVE_ORDER[sort]} LIMIT :limit OFFSET :offset",
            **parameters,
        )

    @router.get("/representatives/{representative_id}")
    def representative(representative_id: SlugPath, database: Database):
        rows = _rows(
            database,
            "SELECT * FROM dash_representatives WHERE representative_id = :id",
            id=representative_id,
        )
        if not rows:
            raise HTTPException(404, "Representative not found")
        positions = _rows(
            database,
            """
            SELECT p.*, t.area, t.label, t.proposition
            FROM dash_representative_positions p
            JOIN policy_topics t ON t.key = p.topic_key
            WHERE p.representative_id = :id
            ORDER BY p.statements DESC, p.topic_key
            """,
            id=representative_id,
        )
        monthly = _rows(
            database,
            "SELECT * FROM dash_representative_monthly "
            "WHERE representative_id = :id ORDER BY month",
            id=representative_id,
        )
        return rows[0] | {"positions": positions, "monthly": monthly}

    @router.get("/topics")
    def topics(
        database: Database,
        area: Slug | None = None,
        sort: Literal[tuple(TOPIC_ORDER)] = "statements",
    ):
        conditions, parameters = [], {}
        if area:
            conditions.append("area = :area")
            parameters["area"] = area
        return _rows(
            database,
            f"SELECT * FROM dash_topics {_where(conditions)} "
            f"ORDER BY {TOPIC_ORDER[sort]}",
            **parameters,
        )

    @router.get("/topics/{topic_key}")
    def topic(
        topic_key: SlugPath,
        database: Database,
        kind: Kind | None = None,
    ):
        rows = _rows(
            database, "SELECT * FROM dash_topics WHERE topic_key = :key", key=topic_key
        )
        if not rows:
            raise HTTPException(404, "Topic not found")
        positions = _rows(
            database,
            """
            SELECT p.*, r.display_name, r.role, r.ward
            FROM dash_representative_positions p
            JOIN representatives r ON r.id = p.representative_id
            WHERE p.topic_key = :key
            ORDER BY p.stance_score DESC NULLS LAST, p.statements DESC,
                     p.representative_id
            """,
            key=topic_key,
        )
        conditions = ["topic_key = :key"]
        parameters = {"key": topic_key}
        if kind:
            conditions.append("kind = :kind")
            parameters["kind"] = kind
        # Collapse bodies so the chart shows one point per month.
        trend = _rows(
            database,
            f"""
            SELECT month, sum(statements) AS statements,
                   sum(support) AS support, sum(oppose) AS oppose,
                   sum(mixed) AS mixed, sum(neutral) AS neutral,
                   sum(stance_score * statements) / nullif(sum(statements), 0)
                       AS stance_score
            FROM dash_topic_trends {_where(conditions)}
            GROUP BY month ORDER BY month
            """,
            **parameters,
        )
        return rows[0] | {"positions": positions, "trend": trend}

    @router.get("/positions")
    def positions(
        database: Database,
        representative_id: Slug | None = None,
        topic_key: Slug | None = None,
        min_statements: Annotated[int, Query(ge=1, le=1000)] = 1,
        manual_only: bool = False,
        limit: Limit = 200,
        offset: Offset = 0,
    ):
        conditions = ["p.statements >= :min_statements"]
        parameters = {
            "min_statements": min_statements,
            "limit": limit,
            "offset": offset,
        }
        if representative_id:
            conditions.append("p.representative_id = :representative_id")
            parameters["representative_id"] = representative_id
        if topic_key:
            conditions.append("p.topic_key = :topic_key")
            parameters["topic_key"] = topic_key
        if manual_only:
            conditions.append("p.verified_speaker > 0")
        return _rows(
            database,
            f"""
            SELECT p.*, r.display_name, t.label AS topic_label, t.area
            FROM dash_representative_positions p
            JOIN representatives r ON r.id = p.representative_id
            JOIN policy_topics t ON t.key = p.topic_key
            {_where(conditions)}
            ORDER BY r.display_name, p.representative_id, t.area, p.topic_key
            LIMIT :limit OFFSET :offset
            """,
            **parameters,
        )

    @router.get("/statements")
    def statements(
        database: Database,
        representative_id: Slug | None = None,
        topic_key: Slug | None = None,
        stance: Stance | None = None,
        kind: Kind | None = None,
        method: Method | None = None,
        attributed: bool | None = None,
        verified_evidence: bool = False,
        min_confidence: Annotated[float, Query(ge=0, le=1)] = 0,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: Limit = 50,
        offset: Offset = 0,
    ):
        conditions = ["confidence >= :min_confidence"]
        parameters = {
            "min_confidence": min_confidence,
            "limit": limit,
            "offset": offset,
        }
        for name, value in (
            ("representative_id", representative_id),
            ("topic_key", topic_key),
            ("stance", stance),
            ("kind", kind),
            ("attribution_method", method),
        ):
            if value is not None:
                conditions.append(f"{name} = :{name}")
                parameters[name] = value
        if attributed is not None:
            conditions.append(
                "representative_id IS NOT NULL"
                if attributed
                else "representative_id IS NULL"
            )
        if verified_evidence:
            conditions.append("evidence_verified")
        if date_from:
            conditions.append("meeting_date >= :date_from")
            parameters["date_from"] = date_from
        if date_to:
            conditions.append("meeting_date <= :date_to")
            parameters["date_to"] = date_to
        return _rows(
            database,
            f"""
            SELECT unit_id, topic_key, stance, score, confidence, summary,
                   evidence, evidence_verified, model, text, start_seconds,
                   end_seconds, source_key, kind, title, body, meeting_date,
                   speaker_label, representative_id, attribution_method,
                   attribution_confidence, sentiment, emotion
            FROM dash_statements {_where(conditions)}
            ORDER BY meeting_date DESC NULLS LAST, source_key, start_seconds,
                     unit_id, topic_key
            LIMIT :limit OFFSET :offset
            """,
            **parameters,
        )

    @router.get("/sources")
    def sources(
        database: Database,
        kind: Kind | None = None,
        limit: Limit = 50,
        offset: Offset = 0,
    ):
        conditions, parameters = [], {"limit": limit, "offset": offset}
        if kind:
            conditions.append("kind = :kind")
            parameters["kind"] = kind
        return _rows(
            database,
            f"SELECT * FROM dash_sources {_where(conditions)} "
            "ORDER BY meeting_date DESC NULLS LAST, source_key "
            "LIMIT :limit OFFSET :offset",
            **parameters,
        )

    return router


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
