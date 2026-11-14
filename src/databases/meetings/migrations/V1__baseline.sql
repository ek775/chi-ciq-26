-- Baseline schema. Every statement is idempotent: Flyway baselines a
-- database that the earlier Python migration created at version 0, and
-- then applies this file to it.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS meetings (
	id UUID NOT NULL,
	source_path VARCHAR NOT NULL,
	filename VARCHAR NOT NULL,
	meeting_date DATE NOT NULL,
	meeting_type VARCHAR NOT NULL,
	status VARCHAR NOT NULL,
	recording_duration_seconds DOUBLE PRECISION,
	transcription_model VARCHAR NOT NULL,
	diarization_model VARCHAR NOT NULL,
	text_embedding_model VARCHAR NOT NULL,
	voice_embedding_model VARCHAR NOT NULL,
	text_embedding_dimensions INTEGER,
	voice_embedding_dimensions INTEGER,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	CHECK (status IN ('processing', 'completed', 'incomplete')),
	CHECK (recording_duration_seconds >= 0 AND recording_duration_seconds < 'Infinity'::double precision)
);

CREATE TABLE IF NOT EXISTS speakers (
	id UUID NOT NULL,
	meeting_id UUID NOT NULL,
	label VARCHAR NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	UNIQUE (meeting_id, label),
	UNIQUE (meeting_id, id),
	FOREIGN KEY(meeting_id) REFERENCES meetings (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS published_sources (
	key VARCHAR NOT NULL,
	content_sha256 VARCHAR NOT NULL,
	meeting_id UUID NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (key),
	CHECK (length(content_sha256) = 64),
	UNIQUE (meeting_id),
	FOREIGN KEY(meeting_id) REFERENCES meetings (id)
);

CREATE TABLE IF NOT EXISTS transcript_segments (
	id UUID NOT NULL,
	meeting_id UUID NOT NULL,
	speaker_id UUID NOT NULL,
	sequence INTEGER NOT NULL,
	start_seconds DOUBLE PRECISION NOT NULL,
	end_seconds DOUBLE PRECISION NOT NULL,
	text VARCHAR NOT NULL,
	text_embedding VECTOR NOT NULL,
	voice_embedding VECTOR NOT NULL,
	created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
	PRIMARY KEY (id),
	FOREIGN KEY(meeting_id, speaker_id) REFERENCES speakers (meeting_id, id) ON DELETE CASCADE,
	UNIQUE (meeting_id, sequence),
	CHECK (start_seconds >= 0 AND end_seconds > start_seconds),
	CHECK (sequence >= 0),
	FOREIGN KEY(meeting_id) REFERENCES meetings (id) ON DELETE CASCADE
);

ALTER TABLE meetings ADD COLUMN IF NOT EXISTS recording_duration_seconds double precision CHECK (recording_duration_seconds >= 0 AND recording_duration_seconds < 'Infinity'::double precision);

COMMENT ON COLUMN meetings.recording_duration_seconds IS 'Decoded first audio track duration in seconds; NULL before extraction.';

COMMENT ON COLUMN meetings.created_at IS 'Database audit time, not a recording offset.';

COMMENT ON COLUMN meetings.updated_at IS 'Database audit time, not a recording offset.';

CREATE INDEX IF NOT EXISTS ix_speakers_meeting_id ON speakers (meeting_id);

COMMENT ON COLUMN speakers.created_at IS 'Database audit time, not a recording offset.';

CREATE INDEX IF NOT EXISTS ix_transcript_segments_meeting_id ON transcript_segments (meeting_id);

CREATE INDEX IF NOT EXISTS ix_transcript_segments_speaker_id ON transcript_segments (speaker_id);

COMMENT ON COLUMN transcript_segments.start_seconds IS 'Inclusive offset in seconds from the decoded audio track''s start.';

COMMENT ON COLUMN transcript_segments.end_seconds IS 'Exclusive offset in seconds from the decoded audio track''s start.';

COMMENT ON COLUMN transcript_segments.created_at IS 'Database audit time, not a recording offset.';
