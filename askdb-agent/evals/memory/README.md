# Memory evaluation fixtures

`askdb_tpcc_manual_cases.json` is an offline evaluation fixture prepared from
the user-provided `manual_cases_tpcc_draft.csv` for
`jdbc:mysql://localhost:3306/askdb_tpcc`.

The fixture contains 22 approved, supported NL-to-SQL cases and 8 clarification
or security gate cases. Query SQL is rewritten from physical `bmsql_*` tables
to the Wren model names and uses named, typed parameters. Example row values,
result evidence, reviewer identity, and snapshot metadata are omitted from the
fixture.

`askdb_tpcc_recall_gold_draft.json` combines those 22 query cases with 20
schema cases derived from the compiled MDL, 8 no-match prompts, and 2 cross-source
isolation cases. The user reviewed and approved all 52 case labels on
2026-10-04. Six damaged schema descriptions have been regenerated from
`wren-project/target/mdl.json`. Its source/revision IDs are
offline placeholders. A synthetic control-source document exists only to
exercise source isolation.

From `askdb-agent/`, run the offline diagnostic:

```bash
.venv/bin/python evals/memory/run_recall_eval.py
```

Use `--format json` for machine-readable output, and `--output PATH` to save
either format. The command exits with status 2 when the release gate is
ineligible (including while this fixture remains a draft or lacks completed
human-review metadata); status 1 indicates malformed input or a runtime error.
The per-case review worksheet is `evals/memory/askdb_tpcc_recall_review_sheet.csv`.
The completed review decisions, reviewer, date, and all 52 reviewed case IDs are
recorded in the fixture. A passing evaluation accepts the offline retrieval gold
gate only; it does not establish runtime readiness or SQL result correctness.

These fixtures are not the canonical query corpus and are not loaded by the
Agent or Wren memory index. The CSV's external `APPROVED` marker does not
activate an AskDB query example. Before a record can enter online recall, bind it to the
actual data-source ID, active Wren revision, and semantic digest, then use the
existing review, SQL validation, and publication flow. The offline evaluator is
an optional quality check and is not a startup gate. Online recall follows the
persistent-memory master switch: when `ASKDB_AGENT_MEMORY_ENABLED=1` and its
deletion-journal prerequisites are configured, recall is enabled by default.
Set `ASKDB_AGENT_RECALL_ENABLED=0` only to explicitly disable recall. The
`ASKDB_AGENT_RECALL_GOLD_ACCEPTED` and `ASKDB_AGENT_RECALL_RUNTIME_READY`
environment variables are no longer required or read by startup.

Neither fixture is loaded into a memory index. The offline score measures
retrieval selection and source isolation; it does not activate a corpus or
establish SQL result correctness.
