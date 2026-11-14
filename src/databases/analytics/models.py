"""Analytics tables: denormalized sources, attributions, and model outputs.

The analytics database never joins the meetings or documents databases at
query time. Every value a dashboard needs (dates, committee names, text, and
speaker labels) is copied here so the web service needs a single connection.
"""

from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB

EMOTIONS = ("anger", "disgust", "fear", "joy", "neutral", "sadness", "surprise")
SENTIMENTS = ("negative", "neutral", "positive")
STANCES = ("support", "oppose", "mixed", "neutral", "not_addressed")
SOURCE_KINDS = ("meeting", "document")
ATTRIBUTION_METHODS = ("manual", "voice")
# manual: loaded from a user CSV and never overwritten by generation.
# official: generated from the official roster source.
REPRESENTATIVE_ORIGINS = ("manual", "official")
# discovered: an ASR spelling variant mined from source text.
ALIAS_ORIGINS = ("manual", "generated", "discovered")
CANDIDATE_STATUSES = ("matched", "ambiguous", "unmatched")
ROLES = (
    "alderperson",
    "mayor",
    "clerk",
    "treasurer",
    "official",
    "staff",
    "public",
    "other",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(value) for value in values)})"


def _audit(name: str = "updated_at") -> Column:
    return Column(
        name, DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _unit_fk() -> Column:
    return Column(
        "unit_id",
        Uuid,
        ForeignKey("analysis_units.id", ondelete="CASCADE"),
        primary_key=True,
    )


metadata = MetaData()

schema_state = Table(
    "schema_state",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", String, nullable=False),
    _audit(),
)

representatives = Table(
    "representatives",
    metadata,
    Column("id", String, primary_key=True),
    Column("display_name", String, nullable=False),
    Column("role", String, nullable=False),
    Column("ward", Integer),
    Column("active_from", Date),
    Column("active_to", Date),
    Column("origin", String, nullable=False, server_default="manual"),
    Column("source_ref", String),
    _audit(),
    CheckConstraint("id ~ '^[a-z0-9][a-z0-9-]*$'", name="representatives_id_slug"),
    CheckConstraint(
        _in("origin", REPRESENTATIVE_ORIGINS), name="representatives_origin"
    ),
    CheckConstraint(_in("role", ROLES), name="representatives_role"),
    CheckConstraint("ward BETWEEN 1 AND 50", name="representatives_ward"),
    CheckConstraint(
        "active_to IS NULL OR active_from IS NULL OR active_to >= active_from",
        name="representatives_term",
    ),
)

representative_aliases = Table(
    "representative_aliases",
    metadata,
    Column(
        "representative_id",
        String,
        ForeignKey("representatives.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("alias", String, primary_key=True),
    Column("origin", String, nullable=False, server_default="manual"),
    CheckConstraint(
        "alias = lower(alias) AND length(alias) >= 3", name="aliases_normalized"
    ),
    CheckConstraint(_in("origin", ALIAS_ORIGINS), name="aliases_origin"),
)

# Title-prefixed names found in source text, for roster review. Private: not
# granted to the dashboard role.
roster_candidates = Table(
    "roster_candidates",
    metadata,
    Column("name", String, primary_key=True),
    Column("example", String, nullable=False),
    Column("occurrences", Integer, nullable=False),
    Column("sources", Integer, nullable=False),
    Column("status", String, nullable=False),
    Column(
        "representative_id",
        String,
        ForeignKey("representatives.id", ondelete="SET NULL"),
    ),
    Column("similarity", Float),
    _audit(),
    CheckConstraint(_in("status", CANDIDATE_STATUSES), name="candidates_status"),
)

sources = Table(
    "sources",
    metadata,
    Column("key", String, primary_key=True),
    Column("kind", String, nullable=False),
    Column("source_ref", String, nullable=False),
    Column("signature", String, nullable=False),
    Column("title", String, nullable=False),
    Column("body", String, nullable=False),
    Column("meeting_date", Date),
    Column("duration_seconds", Float),
    Column("text_embedding_model", String, nullable=False),
    Column("voice_embedding_model", String),
    _audit("synced_at"),
    CheckConstraint(_in("kind", SOURCE_KINDS), name="sources_kind"),
    Index("sources_meeting_date", "meeting_date"),
    Index("sources_body", "body"),
)

speakers = Table(
    "speakers",
    metadata,
    Column("id", Uuid, primary_key=True, default=uuid4),
    Column(
        "source_key",
        String,
        ForeignKey("sources.key", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("label", String, nullable=False),
    Column("segment_count", Integer, nullable=False),
    Column("speaking_seconds", Float, nullable=False),
    Column("voice_embedding_model", String, nullable=False),
    Column("voice_centroid", Vector()),
    UniqueConstraint("source_key", "label"),
    CheckConstraint("segment_count > 0 AND speaking_seconds >= 0"),
)

manual_speaker_labels = Table(
    "manual_speaker_labels",
    metadata,
    Column("source_key", String, primary_key=True),
    Column("speaker_label", String, primary_key=True),
    Column("source_signature", String, nullable=False),
    Column(
        "representative_id",
        String,
        ForeignKey("representatives.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("note", String),
    _audit("labeled_at"),
)

speaker_attributions = Table(
    "speaker_attributions",
    metadata,
    Column(
        "speaker_id",
        Uuid,
        ForeignKey("speakers.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "representative_id",
        String,
        ForeignKey("representatives.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("method", String, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("similarity", Float),
    Column("margin", Float),
    _audit("attributed_at"),
    CheckConstraint(_in("method", ATTRIBUTION_METHODS), name="attribution_method"),
    CheckConstraint("confidence BETWEEN 0 AND 1", name="attribution_confidence"),
    Index("speaker_attributions_representative", "representative_id"),
)

voiceprints = Table(
    "voiceprints",
    metadata,
    Column(
        "representative_id",
        String,
        ForeignKey("representatives.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("voice_embedding_model", String, primary_key=True),
    Column("embedding", Vector(), nullable=False),
    Column("speaker_count", Integer, nullable=False),
    Column("speaking_seconds", Float, nullable=False),
    _audit(),
)

analysis_units = Table(
    "analysis_units",
    metadata,
    Column("id", Uuid, primary_key=True, default=uuid4),
    Column(
        "source_key",
        String,
        ForeignKey("sources.key", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("sequence", Integer, nullable=False),
    Column("speaker_id", Uuid, ForeignKey("speakers.id", ondelete="SET NULL")),
    Column("start_seconds", Float),
    Column("end_seconds", Float),
    Column("text", String, nullable=False),
    Column("text_sha256", String(64), nullable=False),
    Column("word_count", Integer, nullable=False),
    Column("text_embedding", Vector(), nullable=False),
    Column("mentions_roster_sha256", String(64)),
    UniqueConstraint("source_key", "sequence"),
    CheckConstraint("sequence >= 0 AND word_count >= 0"),
    CheckConstraint(
        "end_seconds IS NULL OR start_seconds IS NULL OR end_seconds >= start_seconds"
    ),
    Index("analysis_units_speaker", "speaker_id"),
)

sentiment_results = Table(
    "sentiment_results",
    metadata,
    _unit_fk(),
    Column("model", String, nullable=False),
    Column("label", String, nullable=False),
    Column("score", Float, nullable=False),
    *(Column(name, Float, nullable=False) for name in SENTIMENTS),
    _audit("analyzed_at"),
    CheckConstraint(_in("label", SENTIMENTS), name="sentiment_label"),
    CheckConstraint("score BETWEEN -1 AND 1", name="sentiment_score"),
)

emotion_results = Table(
    "emotion_results",
    metadata,
    _unit_fk(),
    Column("model", String, nullable=False),
    Column("label", String, nullable=False),
    *(Column(name, Float, nullable=False) for name in EMOTIONS),
    _audit("analyzed_at"),
    CheckConstraint(_in("label", EMOTIONS), name="emotion_label"),
)

policy_topics = Table(
    "policy_topics",
    metadata,
    Column("key", String, primary_key=True),
    Column("area", String, nullable=False),
    Column("label", String, nullable=False),
    Column("proposition", String, nullable=False),
    Column("description", String, nullable=False),
    Column("definition_sha256", String(64), nullable=False),
    Column("active", Boolean, nullable=False, server_default="true"),
    _audit(),
    CheckConstraint("key ~ '^[a-z0-9][a-z0-9-]*$'", name="policy_topics_key_slug"),
)

topic_embeddings = Table(
    "topic_embeddings",
    metadata,
    Column(
        "topic_key",
        String,
        ForeignKey("policy_topics.key", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("embedding_model", String, primary_key=True),
    Column("definition_sha256", String(64), nullable=False),
    Column("embedding", Vector(), nullable=False),
)

topic_relevance = Table(
    "topic_relevance",
    metadata,
    _unit_fk(),
    Column(
        "topic_key",
        String,
        ForeignKey("policy_topics.key", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("similarity", Float, nullable=False),
    Column("rank", Integer, nullable=False),
    Index("topic_relevance_topic", "topic_key"),
)

stance_results = Table(
    "stance_results",
    metadata,
    _unit_fk(),
    Column(
        "topic_key",
        String,
        ForeignKey("policy_topics.key", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("model", String, nullable=False),
    Column("prompt_version", String, nullable=False),
    Column("definition_sha256", String(64), nullable=False),
    Column("stance", String, nullable=False),
    Column("score", Float, nullable=False),
    Column("confidence", Float, nullable=False),
    Column("summary", String),
    Column("evidence", String),
    Column("evidence_verified", Boolean, nullable=False),
    _audit("analyzed_at"),
    CheckConstraint(_in("stance", STANCES), name="stance_label"),
    CheckConstraint("score BETWEEN -1 AND 1", name="stance_score"),
    CheckConstraint("confidence BETWEEN 0 AND 1", name="stance_confidence"),
    Index("stance_results_topic", "topic_key"),
)

representative_mentions = Table(
    "representative_mentions",
    metadata,
    _unit_fk(),
    Column(
        "representative_id",
        String,
        ForeignKey("representatives.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("mention_count", Integer, nullable=False),
    CheckConstraint("mention_count > 0"),
    Index("representative_mentions_representative", "representative_id"),
)

analysis_runs = Table(
    "analysis_runs",
    metadata,
    Column("id", Uuid, primary_key=True, default=uuid4),
    _audit("started_at"),
    Column("finished_at", DateTime(timezone=True)),
    Column("status", String, nullable=False, server_default="running"),
    Column("config", JSONB, nullable=False),
    Column("counts", JSONB, nullable=False, server_default="{}"),
    Column("error", String),
    CheckConstraint(
        "status IN ('running', 'completed', 'failed')", name="analysis_runs_status"
    ),
)
