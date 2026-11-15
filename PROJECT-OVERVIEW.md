# Alder Audit: Making Chicago City Council Meetings Searchable

Alder Audit is a civic information project that turns Chicago City Council
meeting recordings and related documents into searchable public records. Its
goal is to make the discussions behind local decisions easier to find and
understand, including questions, explanations, and commitments that may not
appear in a recorded individual vote.

## From recordings to evidence

The project acquires meeting videos, transcribes their audio, and separates
speech into speaker segments. It stores published transcripts in PostgreSQL
with pgvector, alongside a separate database of ingested reference documents.
A read-only web application lets users browse meetings and search transcript
text. Its home page also includes an informational ward lookup using the City
of Chicago's service.

An independent tracker builds a cited dataset of speech, commitments, and
official actions. It checks candidate quotations against the referenced
transcript segment and links evidence back to the recording. Reviewed
follow-ups and meeting digests can be included and published for the web UI.
The current tracker requires prepared candidates and supporting evidence;
it is not yet a fully automated end-to-end meeting analysis.

## Trust and interpretation

The intended public-facing standard is to show only quotations verified
word-for-word against the transcript and to leave a speaker unnamed when
their identity is unconfirmed. Recording links and source references let
readers examine the evidence. The tracker verifies against the transcript,
which is itself a transcription of the recording rather than a guarantee
that every word was heard correctly.

The project also contains an experimental analytics dashboard that estimates
topics, sentiment, emotion, and policy stances. Those outputs are model
interpretations, not verified positions or a substitute for the underlying
record. Voice-based speaker matches are inferred rather than confirmed.

## Computing at scale

Built for an HPC and AI hackathon, the system uses containerized pipelines and
CIQ Fuzzball to schedule work across two Arm-based Lenovo ThinkStation PGX
machines, each with one GPU. Transcription is split into shards, document
ingestion and analytics run as separate jobs, and a vLLM model service supports
stance analysis. Persistent databases, backups, reader roles, and readiness
checks support the final private web service. The design keeps each GPU model
within a single machine rather than assuming GPUs can be shared.

The longer-term direction is a neutral, evidence-linked commitment tracker
and concise meeting digests: do the expensive processing once, then make the
verified material easy for residents to explore.
