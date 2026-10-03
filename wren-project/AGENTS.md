# AGENTS.md

This project uses [Wren Engine](https://github.com/Canner/WrenAI) as the semantic layer for data querying. Queries are written against MDL model names, not raw database tables.

## Answering data questions

When the user asks about data, metrics, reports, or business questions, follow this workflow:

1. `wren memory fetch -q "<question>"` — get relevant schema context
2. `wren memory recall -q "<question>" --limit 3` — find similar past queries
3. Write SQL using model names from the MDL (not raw table names)
4. `wren --sql "<sql>"` — execute through the semantic layer
5. `wren memory store --nl "<question>" --sql "<sql>"` — store confirmed results

If this is the first query in the session, also run `wren context instructions` to load business rules.

## Modifying the data model

When the user wants to add models, change schema, or onboard a new table:

1. Edit YAML files in `models/`, `views/`, or `relationships.yml`
2. `wren context validate` — check structure
3. `wren context build` — compile to `target/mdl.json`
4. `wren memory index` — re-index schema for search

## Prerequisites

This project requires the `wren` CLI. Install with your data source extra:

```bash
pip install "wrenai[postgres,memory,ui]"
```

Replace `postgres` with your data source (`mysql`, `bigquery`, `snowflake`, `clickhouse`, `trino`, `mssql`, `databricks`, `redshift`, `spark`, `athena`, `oracle`). The `memory` extra enables semantic search; `ui` enables the interactive UI.

See https://docs.getwren.ai/oss/engine/get_started/installation for full setup.

## Quick reference

| Task | Command |
|------|---------|
| Run a query | `wren --sql "SELECT ..."` |
| Preview planned SQL | `wren dry-plan --sql "SELECT ..."` |
| Show available models | `wren context show` |
| Check connection | `wren profile debug` |
| Check memory index | `wren memory status` |
| Rebuild after changes | `wren context build && wren memory index` |
