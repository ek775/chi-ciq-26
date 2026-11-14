# Database containers

[Project overview](../../README.md) | [Meetings](meetings/README.md) |
[Documents](documents/README.md)

Database lifetimes are independent of finite ingestion jobs and replaceable web
deployments. Each database needs its own persistent storage, connection secret,
and backup destination. Never mount one PostgreSQL data directory in concurrent
server containers.

- [Meetings](meetings/README.md): implemented PostgreSQL/pgvector schema and
  maintenance commands. The server uses the upstream `pgvector/pgvector:pg17`
  image; the module Dockerfile builds migration/check/backup jobs, not the server.
- [Documents](documents/README.md): implemented PostgreSQL/pgvector registry and
  per-directory chunk tables, with independent migration/check/backup jobs.
  The `documents` Compose profile uses separate storage and credentials.

Run backup before migration and after successful ingestion. Never restore with
`--clean` into the live database; restore into a new empty database. Detailed
commands and safety contracts belong in the [meetings guide](meetings/README.md).
