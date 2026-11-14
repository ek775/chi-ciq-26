-- Repeatable migration: Flyway applies this file again when its content
-- changes. It drops and rebuilds every dashboard view, then restores the
-- grants of the dashboard role, which a rebuild removes.

DROP MATERIALIZED VIEW IF EXISTS dash_representatives CASCADE;
DROP MATERIALIZED VIEW IF EXISTS dash_representative_positions CASCADE;
DROP MATERIALIZED VIEW IF EXISTS dash_representative_monthly CASCADE;
DROP MATERIALIZED VIEW IF EXISTS dash_topics CASCADE;
DROP MATERIALIZED VIEW IF EXISTS dash_topic_trends CASCADE;
DROP MATERIALIZED VIEW IF EXISTS dash_sources CASCADE;
DROP VIEW IF EXISTS dash_statements CASCADE;
DROP VIEW IF EXISTS attributed_units CASCADE;
DROP VIEW IF EXISTS current_stances CASCADE;

CREATE VIEW current_stances AS
SELECT st.unit_id, st.topic_key, st.stance, st.score, st.confidence,
       st.summary, st.evidence, st.evidence_verified, st.model,
       st.analyzed_at
FROM stance_results st
JOIN policy_topics t
  ON t.key = st.topic_key
 AND t.active
 AND t.definition_sha256 = st.definition_sha256
WHERE st.stance <> 'not_addressed';

CREATE VIEW attributed_units AS
SELECT u.id AS unit_id, u.source_key, u.speaker_id, a.representative_id,
       a.method AS attribution_method,
       a.confidence AS attribution_confidence,
       coalesce(u.end_seconds - u.start_seconds, 0) AS seconds
FROM analysis_units u
JOIN speaker_attributions a ON a.speaker_id = u.speaker_id;

CREATE VIEW dash_statements AS
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
LEFT JOIN emotion_results e ON e.unit_id = u.id;

CREATE MATERIALIZED VIEW dash_representatives AS
WITH activity AS (
    SELECT au.representative_id,
           count(DISTINCT au.source_key) AS sources,
           count(*) AS statements,
           count(*) FILTER (WHERE au.attribution_method = 'manual')
               AS verified_statements,
           sum(au.seconds) AS speaking_seconds,
           avg(s.score) AS mean_sentiment,
           avg(e.anger) AS anger, avg(e.disgust) AS disgust, avg(e.fear) AS fear, avg(e.joy) AS joy, avg(e.neutral) AS neutral, avg(e.sadness) AS sadness, avg(e.surprise) AS surprise,
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
       a.mean_sentiment, a.anger, a.disgust, a.fear, a.joy, a.neutral, a.sadness, a.surprise,
       (SELECT v.k FROM (VALUES ('anger', a.anger), ('disgust', a.disgust), ('fear', a.fear), ('joy', a.joy), ('neutral', a.neutral), ('sadness', a.sadness), ('surprise', a.surprise)) AS v(k, x) WHERE v.x IS NOT NULL ORDER BY v.x DESC, v.k LIMIT 1) AS dominant_emotion,
       coalesce(p.positions, 0) AS positions,
       coalesce(p.topics, 0) AS topics,
       coalesce(m.mentions, 0) AS mentions,
       a.first_seen, a.last_seen
FROM representatives r
LEFT JOIN activity a ON a.representative_id = r.id
LEFT JOIN positions p ON p.representative_id = r.id
LEFT JOIN mentions m ON m.representative_id = r.id;
CREATE UNIQUE INDEX dash_representatives_key ON dash_representatives (representative_id);

CREATE MATERIALIZED VIEW dash_representative_positions AS
SELECT au.representative_id, cs.topic_key,
       count(*) AS statements,
       count(DISTINCT au.source_key) AS sources,
       count(*) FILTER (WHERE cs.stance = 'support') AS support, count(*) FILTER (WHERE cs.stance = 'oppose') AS oppose, count(*) FILTER (WHERE cs.stance = 'mixed') AS mixed, count(*) FILTER (WHERE cs.stance = 'neutral') AS neutral,
       count(*) FILTER (WHERE cs.evidence_verified) AS verified_evidence,
       count(*) FILTER (WHERE au.attribution_method = 'manual')
           AS verified_speaker,
       sum(cs.score * cs.confidence) / nullif(sum(cs.confidence), 0) AS stance_score,
       avg(cs.confidence) AS mean_confidence,
       avg(s.score) AS mean_sentiment,
       min(src.meeting_date) AS first_date,
       max(src.meeting_date) AS last_date
FROM current_stances cs
JOIN attributed_units au ON au.unit_id = cs.unit_id
JOIN sources src ON src.key = au.source_key
LEFT JOIN sentiment_results s ON s.unit_id = cs.unit_id
GROUP BY au.representative_id, cs.topic_key;
CREATE UNIQUE INDEX dash_representative_positions_key ON dash_representative_positions (representative_id, topic_key);

CREATE MATERIALIZED VIEW dash_representative_monthly AS
SELECT au.representative_id,
       date_trunc('month', src.meeting_date)::date AS month,
       count(*) AS statements,
       sum(au.seconds) AS speaking_seconds,
       avg(s.score) AS mean_sentiment,
       avg(e.anger) AS anger, avg(e.disgust) AS disgust, avg(e.fear) AS fear, avg(e.joy) AS joy, avg(e.neutral) AS neutral, avg(e.sadness) AS sadness, avg(e.surprise) AS surprise
FROM attributed_units au
JOIN sources src ON src.key = au.source_key
LEFT JOIN sentiment_results s ON s.unit_id = au.unit_id
LEFT JOIN emotion_results e ON e.unit_id = au.unit_id
WHERE src.meeting_date IS NOT NULL
GROUP BY au.representative_id, date_trunc('month', src.meeting_date);
CREATE UNIQUE INDEX dash_representative_monthly_key ON dash_representative_monthly (representative_id, month);

CREATE MATERIALIZED VIEW dash_topics AS
WITH stances AS (
    SELECT cs.topic_key, count(*) AS statements,
           count(DISTINCT u.source_key) AS sources,
           count(*) FILTER (WHERE cs.stance = 'support') AS support, count(*) FILTER (WHERE cs.stance = 'oppose') AS oppose, count(*) FILTER (WHERE cs.stance = 'mixed') AS mixed, count(*) FILTER (WHERE cs.stance = 'neutral') AS neutral, sum(cs.score * cs.confidence) / nullif(sum(cs.confidence), 0) AS stance_score,
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
WHERE p.active;
CREATE UNIQUE INDEX dash_topics_key ON dash_topics (topic_key);

CREATE MATERIALIZED VIEW dash_topic_trends AS
SELECT cs.topic_key,
       date_trunc('month', src.meeting_date)::date AS month,
       src.kind, src.body,
       count(*) AS statements,
       count(DISTINCT src.key) AS sources,
       count(*) FILTER (WHERE cs.stance = 'support') AS support, count(*) FILTER (WHERE cs.stance = 'oppose') AS oppose, count(*) FILTER (WHERE cs.stance = 'mixed') AS mixed, count(*) FILTER (WHERE cs.stance = 'neutral') AS neutral, sum(cs.score * cs.confidence) / nullif(sum(cs.confidence), 0) AS stance_score,
       avg(s.score) AS mean_sentiment
FROM current_stances cs
JOIN analysis_units u ON u.id = cs.unit_id
JOIN sources src ON src.key = u.source_key
LEFT JOIN sentiment_results s ON s.unit_id = cs.unit_id
WHERE src.meeting_date IS NOT NULL
GROUP BY cs.topic_key, date_trunc('month', src.meeting_date),
         src.kind, src.body;
CREATE UNIQUE INDEX dash_topic_trends_key ON dash_topic_trends (topic_key, month, kind, body);

CREATE MATERIALIZED VIEW dash_sources AS
WITH units AS (
    SELECT u.source_key, count(*) AS units,
           count(a.speaker_id) AS attributed_units,
           avg(s.score) AS mean_sentiment,
           avg(e.anger) AS anger, avg(e.disgust) AS disgust, avg(e.fear) AS fear, avg(e.joy) AS joy, avg(e.neutral) AS neutral, avg(e.sadness) AS sadness, avg(e.surprise) AS surprise
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
       a.mean_sentiment, a.anger, a.disgust, a.fear, a.joy, a.neutral, a.sadness, a.surprise,
       (SELECT v.k FROM (VALUES ('anger', a.anger), ('disgust', a.disgust), ('fear', a.fear), ('joy', a.joy), ('neutral', a.neutral), ('sadness', a.sadness), ('surprise', a.surprise)) AS v(k, x) WHERE v.x IS NOT NULL ORDER BY v.x DESC, v.k LIMIT 1) AS dominant_emotion,
       coalesce(t.top_topics, ARRAY[]::varchar[]) AS top_topics
FROM sources src
LEFT JOIN units a ON a.source_key = src.key
LEFT JOIN voices v ON v.source_key = src.key
LEFT JOIN topics t ON t.source_key = src.key;
CREATE UNIQUE INDEX dash_sources_key ON dash_sources (source_key);

-- The dashboard role never sees voice centroids, embeddings, or manual notes.
DO $grant$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'analytics_reader') THEN
    GRANT SELECT ON
      schema_state,
      analysis_runs,
      representatives,
      policy_topics,
      dash_statements,
      dash_representatives,
      dash_representative_positions,
      dash_representative_monthly,
      dash_topics,
      dash_topic_trends,
      dash_sources
    TO analytics_reader;
  END IF;
END
$grant$;
