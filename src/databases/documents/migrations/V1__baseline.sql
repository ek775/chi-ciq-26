-- Baseline schema. Every statement is idempotent: Flyway baselines a
-- database that the earlier Python migration created at version 0, and
-- then applies this file to it.

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS document_directories (
	key VARCHAR NOT NULL,
	namespace VARCHAR NOT NULL,
	relative_path VARCHAR NOT NULL,
	parent_key VARCHAR,
	table_name VARCHAR(63) NOT NULL,
	PRIMARY KEY (key),
	UNIQUE (namespace, relative_path),
	FOREIGN KEY(parent_key) REFERENCES document_directories (key),
	UNIQUE (table_name)
);

CREATE TABLE IF NOT EXISTS documents (
	key VARCHAR NOT NULL,
	directory_key VARCHAR NOT NULL,
	relative_path VARCHAR NOT NULL,
	filename VARCHAR NOT NULL,
	format VARCHAR NOT NULL,
	content_sha256 VARCHAR(64) NOT NULL,
	processing_signature VARCHAR NOT NULL,
	text VARCHAR NOT NULL,
	text_embedding_model VARCHAR NOT NULL,
	text_embedding_dimensions INTEGER,
	chunk_count INTEGER NOT NULL,
	updated_at TIMESTAMP WITH TIME ZONE DEFAULT now(),
	PRIMARY KEY (key),
	CHECK (length(content_sha256) = 64),
	CHECK (chunk_count >= 0),
	CHECK (text_embedding_dimensions > 0),
	UNIQUE (directory_key, filename),
	FOREIGN KEY(directory_key) REFERENCES document_directories (key)
);
