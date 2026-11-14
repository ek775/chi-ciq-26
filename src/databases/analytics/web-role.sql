\getenv web_password ANALYTICS_WEB_POSTGRES_PASSWORD

SELECT 'CREATE ROLE analytics_reader'
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'analytics_reader')
\gexec

ALTER ROLE analytics_reader
  WITH LOGIN PASSWORD :'web_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;

GRANT CONNECT ON DATABASE analytics TO analytics_reader;
GRANT USAGE ON SCHEMA public TO analytics_reader;
-- Keep in sync with databases.analytics.views.DASHBOARD_OBJECTS. Base tables
-- with voice centroids, embeddings, and labeling notes stay private.
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
