"""Analytics orchestration: sync, roster, attribute, analyze, and refresh.

Stages are idempotent and incremental. Each source is replaced atomically when
its upstream signature changes; model outputs are recomputed only when the
unit, model, prompt, or topic definition changes. Dashboards are refreshed in
one transaction at the end so readers never see a partially updated rollup.
"""

import argparse
import csv
import logging
import os
import sys
from collections.abc import Callable, Iterator, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import UUID, uuid4

from dotenv import load_dotenv
from sqlalchemy import Numeric, cast, delete, func, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import Engine
from sqlalchemy.sql.elements import TextClause

from databases.analytics.models import (
    EMOTIONS,
    SENTIMENTS,
    analysis_runs,
    analysis_units,
    emotion_results,
    manual_speaker_labels,
    policy_topics,
    representative_aliases,
    representative_mentions,
    representatives,
    roster_candidates,
    sentiment_results,
    sources,
    speaker_attributions,
    speakers,
    stance_results,
    topic_embeddings,
    topic_relevance,
    voiceprints,
)
from databases.analytics.schema import check_schema, connect_database
from databases.analytics.views import refresh_dashboard_views
from shared.embeddings import TextEmbedder, validate_vector
from shared.logging import configure_logging
from shared.sharding import validate_shard

from .attribution import SpeakerVoice, build_voiceprints, match_voices
from .classifiers import (
    DEFAULT_EMOTION_MODEL,
    DEFAULT_SENTIMENT_MODEL,
    TextClassifier,
    to_emotion,
    to_sentiment,
)
from .officials import (
    PORTAL_URL,
    ROSTER_SOURCES,
    discover_candidates,
    discovered_aliases,
    official_roster,
)
from .roster import (
    ROSTER_COLUMNS,
    MentionMatcher,
    Representative,
    SpeakerLabel,
    read_roster,
    read_speaker_labels,
    roster_digest,
)
from .sources import SourceSnapshot
from .stance import StanceAssessment, StanceClassifier, UnitContext
from .taxonomy import Topic, load_taxonomy

logger = logging.getLogger(__name__)

STAGES = (
    "sync",
    "roster",
    "attribute",
    "mentions",
    "sentiment",
    "emotion",
    "topics",
    "stance",
    "refresh",
)
SOURCES = ("meetings", "documents")
RUN_LOCK = 0x43_49_51_41  # "CIQA"
PARALLEL_STAGES = frozenset(
    {"attribute", "mentions", "sentiment", "emotion", "topics", "stance"}
)
SHARDED_STAGES = frozenset({"sentiment", "emotion", "stance"})
UNIT_BUCKETS = 256


def validate_analytics_shard(
    stages: Sequence[str], *, parallel_stage: bool, index: int, count: int
) -> None:
    validate_shard(index, count)
    if count > 1 and (
        count > UNIT_BUCKETS
        or not parallel_stage
        or len(stages) != 1
        or stages[0] not in SHARDED_STAGES
    ):
        raise ValueError(
            "Sharding requires one parallel sentiment, emotion, or stance stage "
            f"and at most {UNIT_BUCKETS} shards"
        )


@contextmanager
def analytics_run_lock(
    engine: Engine,
    stages: Sequence[str] = (),
    *,
    parallel_stage: bool = False,
    shard_index: int = 0,
    shard_count: int = 1,
) -> Iterator[None]:
    validate_analytics_shard(
        stages,
        parallel_stage=parallel_stage,
        index=shard_index,
        count=shard_count,
    )
    sharded = shard_count > 1
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        mode = "_shared" if parallel_stage else ""
        connection.execute(
            text(f"SELECT pg_advisory_lock{mode}(:key)"), {"key": RUN_LOCK}
        )
        acquired = []
        try:
            if parallel_stage:
                locks = set(stages)
                # Stance reads topic relevance and (for attributed scope) speakers.
                if "stance" in locks:
                    locks.update(("topics", "attribute"))
                for stage in sorted(locks):
                    key = STAGES.index(stage) + 1
                    # Shards share their stage gate; stance shares its read gates.
                    shared = sharded and (
                        stage in stages or stage in {"topics", "attribute"}
                    )
                    suffix = "_shared" if shared else ""
                    connection.execute(
                        text(f"SELECT pg_advisory_lock{suffix}(:namespace, :stage)"),
                        {"namespace": RUN_LOCK, "stage": key},
                    )
                    acquired.append((RUN_LOCK, key, suffix))
                    if sharded and stage in stages:
                        # Fixed buckets serialize overlapping layouts, even when
                        # retry workers use a different shard count.
                        for bucket in range(UNIT_BUCKETS):
                            if bucket % shard_count != shard_index:
                                continue
                            bucket_key = key * UNIT_BUCKETS + bucket
                            connection.execute(
                                text("SELECT pg_advisory_lock(:namespace, :stage)"),
                                {"namespace": RUN_LOCK + 1, "stage": bucket_key},
                            )
                            acquired.append((RUN_LOCK + 1, bucket_key, ""))
            yield
        finally:
            for namespace, key, suffix in reversed(acquired):
                connection.execute(
                    text(f"SELECT pg_advisory_unlock{suffix}(:namespace, :stage)"),
                    {"namespace": namespace, "stage": key},
                )
            connection.execute(
                text(f"SELECT pg_advisory_unlock{mode}(:key)"), {"key": RUN_LOCK}
            )


class SourceReader(Protocol):
    kind: str
    prefix: str

    def signatures(self) -> dict[str, str]: ...

    def load(self, key: str) -> SourceSnapshot | None: ...


@dataclass
class AnalyticsConfig:
    stages: tuple[str, ...] = STAGES
    sentiment_model: str = DEFAULT_SENTIMENT_MODEL
    emotion_model: str = DEFAULT_EMOTION_MODEL
    relevance_threshold: float = 0.35
    relevance_top_k: int = 3
    voice_threshold: float = 0.7
    voice_margin: float = 0.05
    stance_scope: str = "all"
    stance_min_words: int = 12
    stance_limit: int | None = None
    stance_concurrency: int = 4
    batch_size: int = 64
    roster_min_occurrences: int = 3
    parallel_stage: bool = False
    shard_index: int = 0
    shard_count: int = 1

    def __post_init__(self):
        unknown = set(self.stages) - set(STAGES)
        if unknown:
            raise ValueError(f"Unknown stages: {sorted(unknown)}")
        if self.parallel_stage and (
            len(self.stages) != 1 or self.stages[0] not in PARALLEL_STAGES
        ):
            raise ValueError(
                "Parallel mode requires one independent analysis stage; "
                "sync, roster, and refresh must run exclusively"
            )
        validate_analytics_shard(
            self.stages,
            parallel_stage=self.parallel_stage,
            index=self.shard_index,
            count=self.shard_count,
        )
        if self.stance_scope not in {"all", "attributed"}:
            raise ValueError("stance_scope must be 'all' or 'attributed'")
        if not -1 <= self.relevance_threshold <= 1 or self.relevance_top_k < 1:
            raise ValueError("Invalid relevance threshold or top-k")
        if self.batch_size < 1 or self.stance_concurrency < 1:
            raise ValueError("Batch size and concurrency must be positive")
        if self.stance_limit is not None and self.stance_limit < 0:
            raise ValueError("Stance limit must be nonnegative")
        if self.roster_min_occurrences < 1:
            raise ValueError("Roster minimum occurrences must be positive")


@dataclass
class Factories:
    embedder: Callable[[str], TextEmbedder] | None = None
    sentiment: Callable[[str], TextClassifier] | None = None
    emotion: Callable[[str], TextClassifier] | None = None
    stance: Callable[[], StanceClassifier] | None = None
    # Current officials; None skips official seeding but still mines aliases.
    roster: Callable[[], Sequence[Representative]] | None = None


@dataclass
class RunCounts:
    values: dict[str, int] = field(default_factory=dict)

    def add(self, key: str, amount: int = 1) -> None:
        self.values[key] = self.values.get(key, 0) + amount


def _chunks(items: Sequence, size: int) -> Iterator[Sequence]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


class AnalyticsPipeline:
    def __init__(
        self,
        engine: Engine,
        readers: Sequence[SourceReader],
        *,
        taxonomy: Sequence[Topic] | None = None,
        factories: Factories | None = None,
        config: AnalyticsConfig | None = None,
    ):
        self.engine = engine
        self.readers = list(readers)
        self.taxonomy = list(taxonomy or load_taxonomy())
        self.factories = factories or Factories()
        self.config = config or AnalyticsConfig()
        self.counts = RunCounts()

    @contextmanager
    def _run_lock(self) -> Iterator[None]:
        with analytics_run_lock(
            self.engine,
            self.config.stages,
            parallel_stage=self.config.parallel_stage,
            shard_index=self.config.shard_index,
            shard_count=self.config.shard_count,
        ):
            yield

    def _unit_shard(self, alias: str = "analysis_units") -> TextClause:
        if self.config.shard_count == 1:
            return text("TRUE")
        return text(
            f"(('x' || substr(md5(CAST({alias}.id AS text)), 1, 2))"
            "::bit(8)::int % :shard_count) = :shard_index"
        ).bindparams(
            shard_count=self.config.shard_count, shard_index=self.config.shard_index
        )

    def run(self) -> dict[str, int]:
        self.counts = RunCounts()
        config = asdict(self.config) | {
            "sources": [reader.kind for reader in self.readers],
            "topics": [topic.key for topic in self.taxonomy],
        }
        with self._run_lock():
            run_id = uuid4()
            with self.engine.begin() as connection:
                connection.execute(
                    analysis_runs.insert().values(id=run_id, config=config)
                )
            try:
                for stage in STAGES:
                    if stage in self.config.stages:
                        logger.info("Analytics stage %s started", stage)
                        getattr(self, f"stage_{stage}")()
                        logger.info("Analytics stage %s completed", stage)
            except BaseException as error:
                with self.engine.begin() as connection:
                    connection.execute(
                        update(analysis_runs)
                        .where(analysis_runs.c.id == run_id)
                        .values(
                            status="failed",
                            finished_at=func.now(),
                            counts=self.counts.values,
                            error=f"{type(error).__name__}: {error}"[:2000],
                        )
                    )
                raise
            with self.engine.begin() as connection:
                connection.execute(
                    update(analysis_runs)
                    .where(analysis_runs.c.id == run_id)
                    .values(
                        status="completed",
                        finished_at=func.now(),
                        counts=self.counts.values,
                    )
                )
        logger.info("Analytics run completed: %s", self.counts.values)
        return self.counts.values

    def stage_sync(self) -> None:
        for reader in self.readers:
            upstream = reader.signatures()
            with self.engine.begin() as connection:
                existing = dict(
                    connection.execute(
                        select(sources.c.key, sources.c.signature).where(
                            sources.c.kind == reader.kind
                        )
                    ).all()
                )
                removed = sorted(existing.keys() - upstream.keys())
                if removed:
                    connection.execute(
                        delete(sources).where(sources.c.key.in_(removed))
                    )
                    self.counts.add("sources_removed", len(removed))
            for key, signature in sorted(upstream.items()):
                if existing.get(key) == signature:
                    self.counts.add("sources_unchanged")
                    continue
                snapshot = reader.load(key)
                if snapshot is None:
                    continue
                self._store_source(snapshot)
                self.counts.add("sources_synced")
                self.counts.add("units_synced", len(snapshot.units))

    def _store_source(self, snapshot: SourceSnapshot) -> None:
        with self.engine.begin() as connection:
            connection.execute(delete(sources).where(sources.c.key == snapshot.key))
            connection.execute(
                sources.insert().values(
                    key=snapshot.key,
                    kind=snapshot.kind,
                    source_ref=snapshot.source_ref,
                    signature=snapshot.signature,
                    title=snapshot.title,
                    body=snapshot.body,
                    meeting_date=snapshot.meeting_date,
                    duration_seconds=snapshot.duration_seconds,
                    text_embedding_model=snapshot.text_embedding_model,
                    voice_embedding_model=snapshot.voice_embedding_model,
                )
            )
            speaker_ids = {speaker.label: uuid4() for speaker in snapshot.speakers}
            if snapshot.speakers:
                connection.execute(
                    speakers.insert(),
                    [
                        {
                            "id": speaker_ids[speaker.label],
                            "source_key": snapshot.key,
                            "label": speaker.label,
                            "segment_count": speaker.segment_count,
                            "speaking_seconds": speaker.speaking_seconds,
                            "voice_embedding_model": snapshot.voice_embedding_model
                            or "unknown",
                            "voice_centroid": speaker.centroid,
                        }
                        for speaker in snapshot.speakers
                    ],
                )
            for batch in _chunks(snapshot.units, 500):
                connection.execute(
                    analysis_units.insert(),
                    [
                        {
                            "id": uuid4(),
                            "source_key": snapshot.key,
                            "sequence": unit.sequence,
                            "speaker_id": speaker_ids.get(unit.speaker)
                            if unit.speaker
                            else None,
                            "start_seconds": unit.start,
                            "end_seconds": unit.end,
                            "text": unit.text,
                            "text_sha256": unit.text_sha256,
                            "word_count": unit.word_count,
                            "text_embedding": unit.embedding,
                        }
                        for unit in batch
                    ],
                )

    def stage_attribute(self) -> None:
        with self.engine.begin() as connection:
            connection.execute(delete(speaker_attributions))
            manual = dict(
                connection.execute(
                    select(speakers.c.id, manual_speaker_labels.c.representative_id)
                    .join(
                        sources,
                        (sources.c.key == manual_speaker_labels.c.source_key)
                        & (
                            sources.c.signature
                            == manual_speaker_labels.c.source_signature
                        ),
                    )
                    .join(
                        speakers,
                        (speakers.c.source_key == manual_speaker_labels.c.source_key)
                        & (speakers.c.label == manual_speaker_labels.c.speaker_label),
                    )
                ).all()
            )
            total_labels = connection.scalar(
                select(func.count()).select_from(manual_speaker_labels)
            )
            if total_labels - len(manual):
                logger.warning(
                    "%d manual speaker labels refer to superseded or missing "
                    "speakers; relabel them with export-speakers",
                    total_labels - len(manual),
                )
                self.counts.add("stale_manual_labels", total_labels - len(manual))
            if manual:
                connection.execute(
                    speaker_attributions.insert(),
                    [
                        {
                            "speaker_id": speaker_id,
                            "representative_id": representative_id,
                            "method": "manual",
                            "confidence": 1.0,
                        }
                        for speaker_id, representative_id in manual.items()
                    ],
                )
            voices = [
                SpeakerVoice(row.id, row.model, row.voice_centroid, row.seconds)
                for row in connection.execute(
                    select(
                        speakers.c.id,
                        speakers.c.voice_embedding_model.label("model"),
                        speakers.c.voice_centroid,
                        speakers.c.speaking_seconds.label("seconds"),
                    ).where(speakers.c.voice_centroid.is_not(None))
                )
            ]
            prints = build_voiceprints(voices, manual)
            connection.execute(delete(voiceprints))
            if prints:
                connection.execute(
                    voiceprints.insert(),
                    [
                        {
                            "representative_id": item.representative_id,
                            "voice_embedding_model": item.model,
                            "embedding": item.embedding,
                            "speaker_count": item.speaker_count,
                            "speaking_seconds": item.speaking_seconds,
                        }
                        for item in prints
                    ],
                )
            matches = match_voices(
                voices,
                prints,
                exclude=set(manual),
                threshold=self.config.voice_threshold,
                margin=self.config.voice_margin,
            )
            if matches:
                connection.execute(
                    speaker_attributions.insert(),
                    [
                        {
                            "speaker_id": match.speaker_id,
                            "representative_id": match.representative_id,
                            "method": "voice",
                            "confidence": max(0.0, min(1.0, match.similarity)),
                            "similarity": match.similarity,
                            "margin": match.margin,
                        }
                        for match in matches
                    ],
                )
        self.counts.add("manual_attributions", len(manual))
        self.counts.add("voice_attributions", len(matches))

    def stage_roster(self) -> None:
        officials = list(self.factories.roster()) if self.factories.roster else []
        with self.engine.begin() as connection:
            if officials:
                self._seed_officials(connection, officials)
            roster = self._stored_roster(connection)
            texts = (
                (row.source_key, row.text)
                for row in connection.execute(
                    select(
                        analysis_units.c.source_key, analysis_units.c.text
                    ).execution_options(yield_per=1000)
                )
            )
            candidates = discover_candidates(
                texts,
                [item for item in roster if item.role == "alderperson"],
                min_occurrences=self.config.roster_min_occurrences,
            )
            connection.execute(
                delete(representative_aliases).where(
                    representative_aliases.c.origin == "discovered"
                )
            )
            aliases = discovered_aliases(candidates)
            if aliases:
                statement = insert(representative_aliases).values(
                    [
                        {
                            "representative_id": rep,
                            "alias": alias,
                            "origin": "discovered",
                        }
                        for rep, alias in sorted(aliases)
                    ]
                )
                connection.execute(statement.on_conflict_do_nothing())
            connection.execute(delete(roster_candidates))
            if candidates:
                connection.execute(
                    roster_candidates.insert(),
                    [
                        {
                            "name": item.name,
                            "example": item.example,
                            "occurrences": item.occurrences,
                            "sources": item.sources,
                            "status": item.status,
                            "representative_id": item.representative_id,
                            "similarity": item.similarity,
                        }
                        for item in candidates
                    ],
                )
        self.counts.add("roster_aliases_discovered", len(aliases))
        self.counts.add(
            "roster_candidates_unmatched",
            sum(item.status == "unmatched" for item in candidates),
        )

    def _seed_officials(self, connection, officials: Sequence[Representative]):
        """Upsert officials unless a manual entry covers the same person or seat."""
        manual = connection.execute(
            select(representatives.c.id, representatives.c.role, representatives.c.ward)
            .where(representatives.c.origin == "manual")
            .where(
                or_(
                    representatives.c.active_to.is_(None),
                    representatives.c.active_to >= func.current_date(),
                )
            )
        ).all()
        manual_ids = set(
            connection.execute(
                select(representatives.c.id).where(representatives.c.origin == "manual")
            ).scalars()
        )
        seats = {
            (row.role, row.ward if row.role == "alderperson" else None)
            for row in manual
        }
        current = [
            item
            for item in officials
            if item.id not in manual_ids
            and (item.role, item.ward if item.role == "alderperson" else None)
            not in seats
        ]
        if current:
            statement = insert(representatives).values(
                [
                    {
                        "id": item.id,
                        "display_name": item.display_name,
                        "role": item.role,
                        "ward": item.ward,
                        "active_to": None,
                        "origin": "official",
                        "source_ref": item.source_ref,
                    }
                    for item in current
                ]
            )
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        name: statement.excluded[name]
                        for name in (
                            "display_name",
                            "role",
                            "ward",
                            "active_to",
                            "source_ref",
                        )
                    }
                    | {"updated_at": func.now()},
                    where=representatives.c.origin != "manual",
                )
            )
        ids = [item.id for item in current]
        retired = connection.execute(
            update(representatives)
            .where(representatives.c.origin == "official")
            .where(representatives.c.id.not_in(ids))
            .where(representatives.c.active_to.is_(None))
            .values(active_to=func.current_date(), updated_at=func.now())
        ).rowcount
        connection.execute(
            delete(representative_aliases)
            .where(representative_aliases.c.origin == "generated")
            .where(representative_aliases.c.representative_id.in_(ids))
        )
        rows = [
            {"representative_id": item.id, "alias": alias, "origin": "generated"}
            for item in current
            for alias in item.aliases
        ]
        if rows:
            connection.execute(
                insert(representative_aliases).values(rows).on_conflict_do_nothing()
            )
        self.counts.add("roster_official", len(current))
        self.counts.add("roster_retired", retired)

    @staticmethod
    def _stored_roster(connection) -> list[Representative]:
        aliases: dict[str, list[str]] = {}
        for rep, alias in connection.execute(
            select(
                representative_aliases.c.representative_id,
                representative_aliases.c.alias,
            ).where(representative_aliases.c.origin != "discovered")
        ):
            aliases.setdefault(rep, []).append(alias)
        return [
            Representative(
                id=row.id,
                display_name=row.display_name,
                role=row.role,
                ward=row.ward,
                aliases=tuple(sorted(aliases.get(row.id, ()))),
            )
            for row in connection.execute(
                select(
                    representatives.c.id,
                    representatives.c.display_name,
                    representatives.c.role,
                    representatives.c.ward,
                )
            )
        ]

    def stage_mentions(self) -> None:
        with self.engine.connect() as connection:
            aliases = [
                tuple(row)
                for row in connection.execute(
                    select(
                        representative_aliases.c.representative_id,
                        representative_aliases.c.alias,
                    )
                )
            ]
        digest = roster_digest(aliases)
        matcher = MentionMatcher(aliases)
        while True:
            with self.engine.begin() as connection:
                rows = connection.execute(
                    select(analysis_units.c.id, analysis_units.c.text)
                    .where(
                        analysis_units.c.mentions_roster_sha256.is_distinct_from(digest)
                    )
                    .order_by(analysis_units.c.id)
                    .limit(500)
                ).all()
                if not rows:
                    return
                ids = [row.id for row in rows]
                connection.execute(
                    delete(representative_mentions).where(
                        representative_mentions.c.unit_id.in_(ids)
                    )
                )
                mentions = [
                    {"unit_id": row.id, "representative_id": rep, "mention_count": n}
                    for row in rows
                    for rep, n in matcher.count(row.text).items()
                ]
                if mentions:
                    connection.execute(representative_mentions.insert(), mentions)
                connection.execute(
                    update(analysis_units)
                    .where(analysis_units.c.id.in_(ids))
                    .values(mentions_roster_sha256=digest)
                )
                self.counts.add("units_scanned_for_mentions", len(rows))
                self.counts.add(
                    "mentions", sum(item["mention_count"] for item in mentions)
                )

    def _classify(self, stage, table, model_name, factory, convert) -> None:
        classifier: TextClassifier | None = None
        while True:
            with self.engine.connect() as connection:
                rows = connection.execute(
                    select(analysis_units.c.id, analysis_units.c.text)
                    .outerjoin(table, table.c.unit_id == analysis_units.c.id)
                    .where(table.c.unit_id.is_(None) | (table.c.model != model_name))
                    .where(self._unit_shard())
                    .order_by(analysis_units.c.id)
                    .limit(self.config.batch_size)
                ).all()
            if not rows:
                return
            if classifier is None:
                if factory is None:
                    raise RuntimeError(f"No {stage} classifier is configured")
                classifier = factory(model_name)
                if classifier.model_name != model_name:
                    raise ValueError(f"{stage} classifier model mismatch")
            outputs = classifier.classify([row.text for row in rows])
            if len(outputs) != len(rows):
                raise ValueError(f"{stage} classifier returned the wrong row count")
            values = [
                {"unit_id": row.id, "model": model_name, **convert(output)}
                for row, output in zip(rows, outputs, strict=True)
            ]
            statement = insert(table).values(values)
            columns = {
                name: statement.excluded[name]
                for name in values[0]
                if name != "unit_id"
            }
            with self.engine.begin() as connection:
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=["unit_id"],
                        set_=columns | {"analyzed_at": func.now()},
                    )
                )
            self.counts.add(f"{stage}_results", len(rows))

    def stage_sentiment(self) -> None:
        def convert(output):
            result = to_sentiment(output)
            return {"label": result.label, "score": result.score} | {
                name: result.probabilities[name] for name in SENTIMENTS
            }

        self._classify(
            "sentiment",
            sentiment_results,
            self.config.sentiment_model,
            self.factories.sentiment,
            convert,
        )

    def stage_emotion(self) -> None:
        def convert(output):
            result = to_emotion(output)
            return {"label": result.label} | {
                name: result.probabilities[name] for name in EMOTIONS
            }

        self._classify(
            "emotion",
            emotion_results,
            self.config.emotion_model,
            self.factories.emotion,
            convert,
        )

    def stage_topics(self) -> None:
        keys = [topic.key for topic in self.taxonomy]
        with self.engine.begin() as connection:
            statement = insert(policy_topics).values(
                [
                    {
                        "key": topic.key,
                        "area": topic.area,
                        "label": topic.label,
                        "proposition": topic.proposition,
                        "description": topic.description,
                        "definition_sha256": topic.definition_sha256,
                        "active": True,
                    }
                    for topic in self.taxonomy
                ]
            )
            connection.execute(
                statement.on_conflict_do_update(
                    index_elements=["key"],
                    set_={
                        name: statement.excluded[name]
                        for name in (
                            "area",
                            "label",
                            "proposition",
                            "description",
                            "definition_sha256",
                            "active",
                        )
                    }
                    | {"updated_at": func.now()},
                )
            )
            connection.execute(
                update(policy_topics)
                .where(policy_topics.c.key.not_in(keys))
                .values(active=False, updated_at=func.now())
            )
            models = (
                connection.execute(select(sources.c.text_embedding_model).distinct())
                .scalars()
                .all()
            )
            current = {
                (row.topic_key, row.embedding_model): row.definition_sha256
                for row in connection.execute(select(topic_embeddings))
            }
        for model in sorted(models):
            stale = [
                topic
                for topic in self.taxonomy
                if current.get((topic.key, model)) != topic.definition_sha256
            ]
            if not stale:
                continue
            if self.factories.embedder is None:
                raise RuntimeError("No text embedder is configured for topic vectors")
            embedder = self.factories.embedder(model)
            if embedder.model_name != model:
                raise ValueError("Embedder model does not match source vectors")
            rows = [
                {
                    "topic_key": topic.key,
                    "embedding_model": model,
                    "definition_sha256": topic.definition_sha256,
                    "embedding": validate_vector(embedder.embed(topic.embedding_text)),
                }
                for topic in stale
            ]
            statement = insert(topic_embeddings).values(rows)
            with self.engine.begin() as connection:
                connection.execute(
                    statement.on_conflict_do_update(
                        index_elements=["topic_key", "embedding_model"],
                        set_={
                            "definition_sha256": statement.excluded.definition_sha256,
                            "embedding": statement.excluded.embedding,
                        },
                    )
                )
            self.counts.add("topic_embeddings", len(rows))
        with self.engine.begin() as connection:
            connection.execute(text("DELETE FROM topic_relevance"))
            result = connection.execute(
                text(
                    """
                    INSERT INTO topic_relevance (unit_id, topic_key, similarity, rank)
                    SELECT unit_id, topic_key, similarity, rank
                    FROM (
                        SELECT u.id AS unit_id, te.topic_key,
                               1 - (u.text_embedding <=> te.embedding) AS similarity,
                               row_number() OVER (
                                   PARTITION BY u.id
                                   ORDER BY u.text_embedding <=> te.embedding,
                                            te.topic_key
                               ) AS rank
                        FROM analysis_units u
                        JOIN sources s ON s.key = u.source_key
                        JOIN topic_embeddings te
                          ON te.embedding_model = s.text_embedding_model
                        JOIN policy_topics p
                          ON p.key = te.topic_key
                         AND p.active
                         AND p.definition_sha256 = te.definition_sha256
                        WHERE vector_dims(u.text_embedding) = vector_dims(te.embedding)
                    ) ranked
                    WHERE similarity >= :threshold AND rank <= :top_k
                    """
                ),
                {
                    "threshold": self.config.relevance_threshold,
                    "top_k": self.config.relevance_top_k,
                },
            )
            self.counts.add("relevant_unit_topics", result.rowcount)

    def _stance_candidates(self, classifier: StanceClassifier):
        attributed_only = (
            "AND a.speaker_id IS NOT NULL"
            if self.config.stance_scope == "attributed"
            else ""
        )
        limit = "LIMIT :limit" if self.config.stance_limit is not None else ""
        query = text(
            f"""
            SELECT u.id, u.text, s.kind, s.body, s.meeting_date,
                   array_agg(r.topic_key ORDER BY r.rank) AS topics
            FROM topic_relevance r
            JOIN analysis_units u ON u.id = r.unit_id
            JOIN sources s ON s.key = u.source_key
            JOIN policy_topics p ON p.key = r.topic_key
            LEFT JOIN speaker_attributions a ON a.speaker_id = u.speaker_id
            LEFT JOIN stance_results st
              ON st.unit_id = r.unit_id
             AND st.topic_key = r.topic_key
             AND st.model = :model
             AND st.prompt_version = :prompt_version
             AND st.definition_sha256 = p.definition_sha256
            WHERE st.unit_id IS NULL AND u.word_count >= :min_words {attributed_only}
              AND {self._unit_shard("u").text}
            GROUP BY u.id, u.text, s.kind, s.body, s.meeting_date
            ORDER BY bool_or(a.speaker_id IS NOT NULL) DESC,
                     s.meeting_date DESC NULLS LAST, u.id
            {limit}
            """
        )
        parameters = {
            "model": classifier.model_name,
            "prompt_version": classifier.prompt_version,
            "min_words": self.config.stance_min_words,
            "shard_count": self.config.shard_count,
            "shard_index": self.config.shard_index,
        }
        if self.config.stance_limit is not None:
            parameters["limit"] = self.config.stance_limit
        with self.engine.connect() as connection:
            return connection.execute(query, parameters).all()

    def stage_stance(self) -> None:
        with self.engine.connect() as connection:
            pending = connection.scalar(
                select(func.count())
                .select_from(analysis_units)
                .where(
                    analysis_units.c.word_count >= self.config.stance_min_words,
                    analysis_units.c.id.in_(select(topic_relevance.c.unit_id)),
                    self._unit_shard(),
                )
            )
        if not pending or self.config.stance_limit == 0:
            return
        if self.factories.stance is None:
            raise RuntimeError("No stance classifier (vLLM) is configured")
        classifier = self.factories.stance()
        candidates = self._stance_candidates(classifier)
        topics = {topic.key: topic for topic in self.taxonomy}
        definitions = {key: topic.definition_sha256 for key, topic in topics.items()}

        def assess(row) -> tuple[UUID, list[StanceAssessment] | None]:
            try:
                return row.id, classifier.assess(
                    row.text,
                    [topics[key] for key in row.topics if key in topics],
                    UnitContext(row.kind, row.body, row.meeting_date),
                )
            except Exception:
                logger.warning(
                    "Stance request failed for unit %s", row.id, exc_info=True
                )
                return row.id, None

        failures = 0
        page = self.config.batch_size * self.config.stance_concurrency
        with ThreadPoolExecutor(self.config.stance_concurrency) as executor:
            for batch in _chunks(candidates, page):
                values = []
                for unit_id, assessments in executor.map(assess, batch):
                    if assessments is None:
                        failures += 1
                        continue
                    values.extend(
                        {
                            "unit_id": unit_id,
                            "topic_key": item.topic_key,
                            "model": classifier.model_name,
                            "prompt_version": classifier.prompt_version,
                            "definition_sha256": definitions[item.topic_key],
                            "stance": item.stance,
                            "score": item.score,
                            "confidence": item.confidence,
                            "summary": item.summary,
                            "evidence": item.evidence,
                            "evidence_verified": item.evidence_verified,
                        }
                        for item in assessments
                    )
                if values:
                    statement = insert(stance_results).values(values)
                    with self.engine.begin() as connection:
                        connection.execute(
                            statement.on_conflict_do_update(
                                index_elements=["unit_id", "topic_key"],
                                set_={
                                    name: statement.excluded[name]
                                    for name in values[0]
                                    if name not in {"unit_id", "topic_key"}
                                }
                                | {"analyzed_at": func.now()},
                            )
                        )
                self.counts.add("stance_units", len(batch))
                self.counts.add("stance_results", len(values))
        self.counts.add("stance_failures", failures)
        if candidates and failures == len(candidates):
            raise RuntimeError("Every stance request failed; check the vLLM service")

    def stage_refresh(self) -> None:
        with self.engine.begin() as connection:
            refresh_dashboard_views(connection)


def load_roster(engine: Engine, roster: Sequence[Representative]) -> int:
    """Upsert manual representatives and replace their aliases.

    Manual entries take precedence over official seeding. Aliases discovered
    in transcripts are kept; other representatives are untouched.
    """
    if not roster:
        return 0
    with engine.begin() as connection:
        rows = [
            {
                "id": item.id,
                "display_name": item.display_name,
                "role": item.role,
                "ward": item.ward,
                "active_from": item.active_from,
                "active_to": item.active_to,
                "origin": "manual",
                "source_ref": item.source_ref,
            }
            for item in roster
        ]
        statement = insert(representatives).values(rows)
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    name: statement.excluded[name] for name in rows[0] if name != "id"
                }
                | {"updated_at": func.now()},
            )
        )
        ids = [item.id for item in roster]
        connection.execute(
            delete(representative_aliases)
            .where(representative_aliases.c.representative_id.in_(ids))
            .where(representative_aliases.c.origin != "discovered")
        )
        statement = insert(representative_aliases).values(
            [
                {"representative_id": item.id, "alias": alias, "origin": "manual"}
                for item in roster
                for alias in item.aliases
            ]
        )
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=["representative_id", "alias"],
                set_={"origin": statement.excluded.origin},
            )
        )
    return len(roster)


def export_roster(engine: Engine, output, *, include_inactive: bool = False) -> int:
    """Write the stored roster as CSV that ``roster`` can load back."""
    aliases = (
        select(func.string_agg(representative_aliases.c.alias, ";"))
        .where(representative_aliases.c.representative_id == representatives.c.id)
        .where(representative_aliases.c.origin != "generated")
        .scalar_subquery()
    )
    query = select(
        representatives.c.id,
        representatives.c.display_name,
        representatives.c.role,
        representatives.c.ward,
        aliases.label("aliases"),
        representatives.c.active_from,
        representatives.c.active_to,
    ).order_by(representatives.c.role, representatives.c.ward, representatives.c.id)
    if not include_inactive:
        query = query.where(
            or_(
                representatives.c.active_to.is_(None),
                representatives.c.active_to >= func.current_date(),
            )
        )
    writer = csv.writer(output)
    writer.writerow((*ROSTER_COLUMNS, "active_from", "active_to"))
    count = 0
    with engine.connect() as connection:
        for row in connection.execute(query):
            writer.writerow(["" if value is None else value for value in row])
            count += 1
    return count


CANDIDATE_COLUMNS = (
    "name",
    "status",
    "representative_id",
    "similarity",
    "occurrences",
    "sources",
    "example",
)


def export_candidates(engine: Engine, output) -> int:
    """Write names found after titles in source text, for roster review."""
    writer = csv.writer(output)
    writer.writerow(CANDIDATE_COLUMNS)
    count = 0
    with engine.connect() as connection:
        for row in connection.execute(
            select(*(roster_candidates.c[name] for name in CANDIDATE_COLUMNS)).order_by(
                roster_candidates.c.status.desc(),
                roster_candidates.c.occurrences.desc(),
            )
        ):
            writer.writerow(row)
            count += 1
    return count


def load_speaker_labels(engine: Engine, labels: Sequence[SpeakerLabel]) -> int:
    """Pin labels to the currently synced run of each source, or fail."""
    with engine.begin() as connection:
        known_speakers = {
            (row.source_key, row.label): row.signature
            for row in connection.execute(
                select(
                    speakers.c.source_key, speakers.c.label, sources.c.signature
                ).join(sources, sources.c.key == speakers.c.source_key)
            )
        }
        known_representatives = set(
            connection.execute(select(representatives.c.id)).scalars()
        )
        problems = [
            f"{label.source_key} {label.speaker_label}: unknown speaker"
            for label in labels
            if (label.source_key, label.speaker_label) not in known_speakers
        ] + [
            f"{label.source_key} {label.speaker_label}: unknown representative "
            f"{label.representative_id}"
            for label in labels
            if label.representative_id not in known_representatives
        ]
        if problems:
            raise ValueError("Invalid speaker labels: " + "; ".join(problems[:10]))
        if not labels:
            return 0
        rows = [
            {
                "source_key": label.source_key,
                "speaker_label": label.speaker_label,
                "source_signature": known_speakers[
                    (label.source_key, label.speaker_label)
                ],
                "representative_id": label.representative_id,
                "note": label.note,
            }
            for label in labels
        ]
        statement = insert(manual_speaker_labels).values(rows)
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=["source_key", "speaker_label"],
                set_={
                    "source_signature": statement.excluded.source_signature,
                    "representative_id": statement.excluded.representative_id,
                    "note": statement.excluded.note,
                    "labeled_at": func.now(),
                },
            )
        )
    return len(labels)


EXPORT_COLUMNS = (
    "source_key",
    "meeting_date",
    "body",
    "speaker_label",
    "segment_count",
    "speaking_seconds",
    "representative_id",
    "method",
    "confidence",
    "sample_text",
)


def export_speakers(engine: Engine, output, *, unattributed_only: bool = False) -> int:
    """Write a labeling worksheet; fill representative_id and re-import it."""
    sample = (
        select(func.left(analysis_units.c.text, 240))
        .where(analysis_units.c.speaker_id == speakers.c.id)
        .order_by(analysis_units.c.word_count.desc(), analysis_units.c.sequence)
        .limit(1)
        .scalar_subquery()
    )
    query = (
        select(
            speakers.c.source_key,
            sources.c.meeting_date,
            sources.c.body,
            speakers.c.label.label("speaker_label"),
            speakers.c.segment_count,
            func.round(cast(speakers.c.speaking_seconds, Numeric), 1).label(
                "speaking_seconds"
            ),
            speaker_attributions.c.representative_id,
            speaker_attributions.c.method,
            speaker_attributions.c.confidence,
            sample.label("sample_text"),
        )
        .join(sources, sources.c.key == speakers.c.source_key)
        .outerjoin(
            speaker_attributions, speaker_attributions.c.speaker_id == speakers.c.id
        )
        .order_by(
            sources.c.meeting_date.desc().nulls_last(),
            speakers.c.source_key,
            speakers.c.speaking_seconds.desc(),
        )
    )
    if unattributed_only:
        query = query.where(speaker_attributions.c.speaker_id.is_(None))
    writer = csv.writer(output)
    writer.writerow(EXPORT_COLUMNS)
    count = 0
    with engine.connect() as connection:
        for row in connection.execute(query):
            writer.writerow(row)
            count += 1
    return count


def _source_readers(names: Sequence[str]) -> tuple[list[SourceReader], list[Engine]]:
    from .sources import DocumentsReader, MeetingsReader

    readers: list[SourceReader] = []
    engines: list[Engine] = []
    if "meetings" in names:
        url = os.environ.get("MEETINGS_DATABASE_URL") or os.environ["DATABASE_URL"]
        engines.append(connect_database(url))
        readers.append(MeetingsReader(engines[-1]))
    if "documents" in names:
        engines.append(connect_database(os.environ["DOCUMENTS_DATABASE_URL"]))
        readers.append(DocumentsReader(engines[-1]))
    return readers, engines


def _factories(args) -> Factories:
    def embedder(model: str) -> TextEmbedder:
        from shared.embeddings import SentenceTextEmbedder

        return SentenceTextEmbedder(model, device=args.device)

    def classifier(model: str) -> TextClassifier:
        from .classifiers import TransformersClassifier

        return TransformersClassifier(model, device=args.device)

    def stance() -> StanceClassifier:
        from .stance import VllmStanceClient

        return VllmStanceClient(
            args.vllm_url,
            served_model=args.vllm_model,
            api_key=os.environ.get("VLLM_API_KEY") or None,
            timeout=args.vllm_timeout,
            reasoning_effort=args.vllm_reasoning_effort or None,
            reasoning_tokens=args.vllm_reasoning_tokens,
        )

    def roster() -> list[Representative]:
        return official_roster(args.roster_source, url=args.roster_url)

    return Factories(
        embedder,
        classifier,
        classifier,
        stance,
        roster if args.roster_source != "off" else None,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Policy, sentiment, and emotion analytics for dashboards"
    )
    commands = parser.add_subparsers(dest="command", required=True)
    run = commands.add_parser("run", help="Sync sources and run analysis stages")
    run.add_argument("--sources", nargs="+", choices=SOURCES, default=list(SOURCES))
    run.add_argument("--stages", nargs="+", choices=STAGES, default=list(STAGES))
    run.add_argument("--shard-index", type=int, default=0)
    run.add_argument("--shard-count", type=int, default=1)
    run.add_argument(
        "--parallel-stage",
        action="store_true",
        help="Allow one independent stage to overlap other independent stages",
    )
    run.add_argument("--taxonomy", type=Path)
    run.add_argument("--device", default="cpu")
    run.add_argument("--sentiment-model", default=DEFAULT_SENTIMENT_MODEL)
    run.add_argument("--emotion-model", default=DEFAULT_EMOTION_MODEL)
    run.add_argument(
        "--vllm-url",
        default=os.environ.get("VLLM_BASE_URL", "http://127.0.0.1:8000/v1"),
    )
    run.add_argument(
        "--vllm-model", default=os.environ.get("VLLM_SERVED_MODEL", "local-ai")
    )
    run.add_argument("--vllm-timeout", type=float, default=300.0)
    run.add_argument(
        "--vllm-reasoning-effort",
        default=os.environ.get("VLLM_REASONING_EFFORT", "low"),
        help="chat_template_kwargs reasoning_effort for reasoning models; "
        "empty to omit",
    )
    run.add_argument(
        "--vllm-reasoning-tokens",
        type=int,
        default=int(os.environ.get("VLLM_REASONING_TOKENS", "4096")),
        help="Token budget for thinking, added to the answer budget",
    )
    run.add_argument(
        "--roster-source",
        choices=ROSTER_SOURCES,
        default="portal",
        help="Official roster: Chicago Data Portal (snapshot fallback), "
        "bundled snapshot only, or off",
    )
    run.add_argument("--roster-url", default=PORTAL_URL)
    defaults = AnalyticsConfig()
    run.add_argument(
        "--relevance-threshold", type=float, default=defaults.relevance_threshold
    )
    run.add_argument("--relevance-top-k", type=int, default=defaults.relevance_top_k)
    run.add_argument("--voice-threshold", type=float, default=defaults.voice_threshold)
    run.add_argument("--voice-margin", type=float, default=defaults.voice_margin)
    run.add_argument(
        "--stance-scope", choices=("all", "attributed"), default=defaults.stance_scope
    )
    run.add_argument("--stance-min-words", type=int, default=defaults.stance_min_words)
    run.add_argument("--stance-limit", type=int)
    run.add_argument(
        "--stance-concurrency", type=int, default=defaults.stance_concurrency
    )
    run.add_argument("--batch-size", type=int, default=defaults.batch_size)
    run.add_argument(
        "--roster-min-occurrences",
        type=int,
        default=defaults.roster_min_occurrences,
        help="Mentions needed before a misspelled name becomes an alias",
    )
    roster = commands.add_parser("roster", help="Upsert representatives from CSV")
    roster.add_argument("file", type=Path)
    export_roster_command = commands.add_parser(
        "export-roster", help="Write the stored roster as editable CSV"
    )
    export_roster_command.add_argument("--output", type=Path)
    export_roster_command.add_argument("--include-inactive", action="store_true")
    candidates = commands.add_parser(
        "roster-candidates", help="Write names found in source text for review"
    )
    candidates.add_argument("--output", type=Path)
    labels = commands.add_parser("label-speakers", help="Import manual speaker labels")
    labels.add_argument("file", type=Path)
    export = commands.add_parser("export-speakers", help="Write a labeling worksheet")
    export.add_argument("--output", type=Path)
    export.add_argument("--unattributed-only", action="store_true")
    commands.add_parser("refresh", help="Refresh dashboard views only")
    return parser


def _write_output(path: Path | None, write: Callable) -> int:
    if path is None:
        return write(sys.stdout)
    with path.open("w", newline="", encoding="utf-8") as handle:
        return write(handle)


def main(argv: list[str] | None = None) -> None:
    load_dotenv()
    args = _parser().parse_args(argv)
    configure_logging("analytics")
    engine = connect_database(os.environ["ANALYTICS_DATABASE_URL"])
    try:
        check_schema(engine)
        if args.command == "roster":
            with analytics_run_lock(engine):
                count = load_roster(engine, read_roster(args.file))
            logger.info("Loaded %d representatives", count)
        elif args.command == "label-speakers":
            with analytics_run_lock(engine):
                count = load_speaker_labels(engine, read_speaker_labels(args.file))
            logger.info("Loaded %d manual speaker labels", count)
        elif args.command == "export-speakers":
            count = _write_output(
                args.output,
                lambda handle: export_speakers(
                    engine, handle, unattributed_only=args.unattributed_only
                ),
            )
            logger.info("Exported %d speakers", count)
        elif args.command == "export-roster":
            count = _write_output(
                args.output,
                lambda handle: export_roster(
                    engine, handle, include_inactive=args.include_inactive
                ),
            )
            logger.info("Exported %d representatives", count)
        elif args.command == "roster-candidates":
            count = _write_output(
                args.output, lambda handle: export_candidates(engine, handle)
            )
            logger.info("Exported %d roster candidates", count)
        elif args.command == "refresh":
            with analytics_run_lock(engine), engine.begin() as connection:
                refresh_dashboard_views(connection)
            logger.info("Dashboard views refreshed")
        else:
            config = AnalyticsConfig(
                stages=tuple(args.stages),
                parallel_stage=args.parallel_stage,
                shard_index=args.shard_index,
                shard_count=args.shard_count,
                sentiment_model=args.sentiment_model,
                emotion_model=args.emotion_model,
                relevance_threshold=args.relevance_threshold,
                relevance_top_k=args.relevance_top_k,
                voice_threshold=args.voice_threshold,
                voice_margin=args.voice_margin,
                stance_scope=args.stance_scope,
                stance_min_words=args.stance_min_words,
                stance_limit=args.stance_limit,
                stance_concurrency=args.stance_concurrency,
                batch_size=args.batch_size,
                roster_min_occurrences=args.roster_min_occurrences,
            )
            needs_sources = "sync" in config.stages
            readers, engines = _source_readers(args.sources if needs_sources else ())
            try:
                pipeline = AnalyticsPipeline(
                    engine,
                    readers,
                    taxonomy=load_taxonomy(args.taxonomy),
                    factories=_factories(args),
                    config=config,
                )
                counts = pipeline.run()
                print(counts, flush=True)
            finally:
                for source_engine in engines:
                    source_engine.dispose()
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
