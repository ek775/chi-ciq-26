\getenv reader_role READER_ROLE
\getenv reader_password READER_PASSWORD
SELECT format('CREATE ROLE %I', :'reader_role')
WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = :'reader_role')
\gexec
SELECT format(
  'ALTER ROLE %I WITH LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS',
  :'reader_role', :'reader_password')
\gexec
GRANT USAGE ON SCHEMA public TO :"reader_role";
\getenv grant_source_tables GRANT_SOURCE_TABLES
\if :grant_source_tables
GRANT SELECT ON ALL TABLES IN SCHEMA public TO :"reader_role";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO :"reader_role";
\endif
