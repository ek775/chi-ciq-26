FROM flyway/flyway:13.12.2

COPY src/databases/flyway/flyway.toml /flyway/conf/flyway.toml
COPY src/databases/meetings/migrations /flyway/sql/meetings
COPY src/databases/documents/migrations /flyway/sql/documents
COPY src/databases/analytics/migrations /flyway/sql/analytics

CMD ["info"]
