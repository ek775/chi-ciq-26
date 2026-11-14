-- Baseline schema. Every statement is idempotent: Flyway baselines a
-- database that the earlier Python migration created at version 0, and
-- then applies this file to it.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS schema_state (
	key VARCHAR NOT NULL,
	value VARCHAR NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (key)
);

CREATE TABLE IF NOT EXISTS representatives (
	id VARCHAR NOT NULL,
	display_name VARCHAR NOT NULL,
	role VARCHAR NOT NULL,
	ward INTEGER,
	active_from DATE,
	active_to DATE,
	origin VARCHAR DEFAULT 'manual' NOT NULL,
	source_ref VARCHAR,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CONSTRAINT representatives_id_slug CHECK (id ~ '^[a-z0-9][a-z0-9-]*$'),
	CONSTRAINT representatives_origin CHECK (origin IN ('manual', 'official')),
	CONSTRAINT representatives_role CHECK (role IN ('alderperson', 'mayor', 'clerk', 'treasurer', 'official', 'staff', 'public', 'other')),
	CONSTRAINT representatives_ward CHECK (ward BETWEEN 1 AND 50),
	CONSTRAINT representatives_term CHECK (active_to IS NULL OR active_from IS NULL OR active_to >= active_from)
);

CREATE TABLE IF NOT EXISTS sources (
	key VARCHAR NOT NULL,
	kind VARCHAR NOT NULL,
	source_ref VARCHAR NOT NULL,
	signature VARCHAR NOT NULL,
	title VARCHAR NOT NULL,
	body VARCHAR NOT NULL,
	meeting_date DATE,
	duration_seconds FLOAT,
	text_embedding_model VARCHAR NOT NULL,
	voice_embedding_model VARCHAR,
	synced_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (key),
	CONSTRAINT sources_kind CHECK (kind IN ('meeting', 'document'))
);

CREATE TABLE IF NOT EXISTS policy_topics (
	key VARCHAR NOT NULL,
	area VARCHAR NOT NULL,
	label VARCHAR NOT NULL,
	proposition VARCHAR NOT NULL,
	description VARCHAR NOT NULL,
	definition_sha256 VARCHAR(64) NOT NULL,
	active BOOLEAN DEFAULT 'true' NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (key),
	CONSTRAINT policy_topics_key_slug CHECK (key ~ '^[a-z0-9][a-z0-9-]*$')
);

CREATE TABLE IF NOT EXISTS analysis_runs (
	id UUID NOT NULL,
	started_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	finished_at TIMESTAMP WITH TIME ZONE,
	status VARCHAR DEFAULT 'running' NOT NULL,
	config JSONB NOT NULL,
	counts JSONB DEFAULT '{}' NOT NULL,
	error VARCHAR,
	PRIMARY KEY (id),
	CONSTRAINT analysis_runs_status CHECK (status IN ('running', 'completed', 'failed'))
);

CREATE TABLE IF NOT EXISTS representative_aliases (
	representative_id VARCHAR NOT NULL,
	alias VARCHAR NOT NULL,
	origin VARCHAR DEFAULT 'manual' NOT NULL,
	PRIMARY KEY (representative_id, alias),
	CONSTRAINT aliases_normalized CHECK (alias = lower(alias) AND length(alias) >= 3),
	CONSTRAINT aliases_origin CHECK (origin IN ('manual', 'generated', 'discovered')),
	FOREIGN KEY(representative_id) REFERENCES representatives (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS roster_candidates (
	name VARCHAR NOT NULL,
	example VARCHAR NOT NULL,
	occurrences INTEGER NOT NULL,
	sources INTEGER NOT NULL,
	status VARCHAR NOT NULL,
	representative_id VARCHAR,
	similarity FLOAT,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (name),
	CONSTRAINT candidates_status CHECK (status IN ('matched', 'ambiguous', 'unmatched')),
	FOREIGN KEY(representative_id) REFERENCES representatives (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS speakers (
	id UUID NOT NULL,
	source_key VARCHAR NOT NULL,
	label VARCHAR NOT NULL,
	segment_count INTEGER NOT NULL,
	speaking_seconds FLOAT NOT NULL,
	voice_embedding_model VARCHAR NOT NULL,
	voice_centroid VECTOR,
	PRIMARY KEY (id),
	UNIQUE (source_key, label),
	CHECK (segment_count > 0 AND speaking_seconds >= 0),
	FOREIGN KEY(source_key) REFERENCES sources (key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS manual_speaker_labels (
	source_key VARCHAR NOT NULL,
	speaker_label VARCHAR NOT NULL,
	source_signature VARCHAR NOT NULL,
	representative_id VARCHAR NOT NULL,
	note VARCHAR,
	labeled_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (source_key, speaker_label),
	FOREIGN KEY(representative_id) REFERENCES representatives (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS voiceprints (
	representative_id VARCHAR NOT NULL,
	voice_embedding_model VARCHAR NOT NULL,
	embedding VECTOR NOT NULL,
	speaker_count INTEGER NOT NULL,
	speaking_seconds FLOAT NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (representative_id, voice_embedding_model),
	FOREIGN KEY(representative_id) REFERENCES representatives (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS topic_embeddings (
	topic_key VARCHAR NOT NULL,
	embedding_model VARCHAR NOT NULL,
	definition_sha256 VARCHAR(64) NOT NULL,
	embedding VECTOR NOT NULL,
	PRIMARY KEY (topic_key, embedding_model),
	FOREIGN KEY(topic_key) REFERENCES policy_topics (key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS speaker_attributions (
	speaker_id UUID NOT NULL,
	representative_id VARCHAR NOT NULL,
	method VARCHAR NOT NULL,
	confidence FLOAT NOT NULL,
	similarity FLOAT,
	margin FLOAT,
	attributed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (speaker_id),
	CONSTRAINT attribution_method CHECK (method IN ('manual', 'voice')),
	CONSTRAINT attribution_confidence CHECK (confidence BETWEEN 0 AND 1),
	FOREIGN KEY(speaker_id) REFERENCES speakers (id) ON DELETE CASCADE,
	FOREIGN KEY(representative_id) REFERENCES representatives (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS analysis_units (
	id UUID NOT NULL,
	source_key VARCHAR NOT NULL,
	sequence INTEGER NOT NULL,
	speaker_id UUID,
	start_seconds FLOAT,
	end_seconds FLOAT,
	text VARCHAR NOT NULL,
	text_sha256 VARCHAR(64) NOT NULL,
	word_count INTEGER NOT NULL,
	text_embedding VECTOR NOT NULL,
	mentions_roster_sha256 VARCHAR(64),
	PRIMARY KEY (id),
	UNIQUE (source_key, sequence),
	CHECK (sequence >= 0 AND word_count >= 0),
	CHECK (end_seconds IS NULL OR start_seconds IS NULL OR end_seconds >= start_seconds),
	FOREIGN KEY(source_key) REFERENCES sources (key) ON DELETE CASCADE,
	FOREIGN KEY(speaker_id) REFERENCES speakers (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS sentiment_results (
	unit_id UUID NOT NULL,
	model VARCHAR NOT NULL,
	label VARCHAR NOT NULL,
	score FLOAT NOT NULL,
	negative FLOAT NOT NULL,
	neutral FLOAT NOT NULL,
	positive FLOAT NOT NULL,
	analyzed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (unit_id),
	CONSTRAINT sentiment_label CHECK (label IN ('negative', 'neutral', 'positive')),
	CONSTRAINT sentiment_score CHECK (score BETWEEN -1 AND 1),
	FOREIGN KEY(unit_id) REFERENCES analysis_units (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS emotion_results (
	unit_id UUID NOT NULL,
	model VARCHAR NOT NULL,
	label VARCHAR NOT NULL,
	anger FLOAT NOT NULL,
	disgust FLOAT NOT NULL,
	fear FLOAT NOT NULL,
	joy FLOAT NOT NULL,
	neutral FLOAT NOT NULL,
	sadness FLOAT NOT NULL,
	surprise FLOAT NOT NULL,
	analyzed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (unit_id),
	CONSTRAINT emotion_label CHECK (label IN ('anger', 'disgust', 'fear', 'joy', 'neutral', 'sadness', 'surprise')),
	FOREIGN KEY(unit_id) REFERENCES analysis_units (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS topic_relevance (
	unit_id UUID NOT NULL,
	topic_key VARCHAR NOT NULL,
	similarity FLOAT NOT NULL,
	rank INTEGER NOT NULL,
	PRIMARY KEY (unit_id, topic_key),
	FOREIGN KEY(unit_id) REFERENCES analysis_units (id) ON DELETE CASCADE,
	FOREIGN KEY(topic_key) REFERENCES policy_topics (key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS stance_results (
	unit_id UUID NOT NULL,
	topic_key VARCHAR NOT NULL,
	model VARCHAR NOT NULL,
	prompt_version VARCHAR NOT NULL,
	definition_sha256 VARCHAR(64) NOT NULL,
	stance VARCHAR NOT NULL,
	score FLOAT NOT NULL,
	confidence FLOAT NOT NULL,
	summary VARCHAR,
	evidence VARCHAR,
	evidence_verified BOOLEAN NOT NULL,
	analyzed_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (unit_id, topic_key),
	CONSTRAINT stance_label CHECK (stance IN ('support', 'oppose', 'mixed', 'neutral', 'not_addressed')),
	CONSTRAINT stance_score CHECK (score BETWEEN -1 AND 1),
	CONSTRAINT stance_confidence CHECK (confidence BETWEEN 0 AND 1),
	FOREIGN KEY(unit_id) REFERENCES analysis_units (id) ON DELETE CASCADE,
	FOREIGN KEY(topic_key) REFERENCES policy_topics (key) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS representative_mentions (
	unit_id UUID NOT NULL,
	representative_id VARCHAR NOT NULL,
	mention_count INTEGER NOT NULL,
	PRIMARY KEY (unit_id, representative_id),
	CHECK (mention_count > 0),
	FOREIGN KEY(unit_id) REFERENCES analysis_units (id) ON DELETE CASCADE,
	FOREIGN KEY(representative_id) REFERENCES representatives (id) ON DELETE CASCADE
);

ALTER TABLE representatives ADD COLUMN IF NOT EXISTS origin VARCHAR NOT NULL DEFAULT 'manual' CONSTRAINT representatives_origin CHECK (origin IN ('manual', 'official'));

ALTER TABLE representatives ADD COLUMN IF NOT EXISTS source_ref VARCHAR;

ALTER TABLE representative_aliases ADD COLUMN IF NOT EXISTS origin VARCHAR NOT NULL DEFAULT 'manual' CONSTRAINT aliases_origin CHECK (origin IN ('manual', 'generated', 'discovered'));

CREATE INDEX IF NOT EXISTS sources_meeting_date ON sources (meeting_date);

CREATE INDEX IF NOT EXISTS sources_body ON sources (body);

CREATE INDEX IF NOT EXISTS speaker_attributions_representative ON speaker_attributions (representative_id);

CREATE INDEX IF NOT EXISTS analysis_units_speaker ON analysis_units (speaker_id);

CREATE INDEX IF NOT EXISTS topic_relevance_topic ON topic_relevance (topic_key);

CREATE INDEX IF NOT EXISTS stance_results_topic ON stance_results (topic_key);

CREATE INDEX IF NOT EXISTS representative_mentions_representative ON representative_mentions (representative_id);
