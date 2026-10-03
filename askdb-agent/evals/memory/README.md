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
isolation cases. It meets the evaluator's category-count minimums as a draft;
the MDL-derived and negative cases still need human review, and the dataset has
not been scored. Its source/revision IDs are offline placeholders. A synthetic
control-source document exists only to exercise source isolation.

These fixtures are not the canonical query corpus and are not loaded by the
Agent or Wren memory index. The CSV's external `APPROVED` marker does not
activate an AskDB query example. Before a record can enter online recall, bind it to the
actual data-source ID, active Wren revision, and semantic digest, then use the
existing review, SQL validation, and publication flow. Keep
`wren-project/queries.yml` empty while recall remains behind the evaluation and
runtime-wiring gates. Online recall defaults to disabled. Enabling it requires
`ASKDB_AGENT_RECALL_ENABLED=1`, `ASKDB_AGENT_RECALL_GOLD_ACCEPTED=1`,
`ASKDB_AGENT_RECALL_RUNTIME_READY=1`, `ASKDB_AGENT_MEMORY_ENABLED=1`, and its
deletion journal. Set the gold acceptance flag only after the gold cases are
reviewed and the offline recall thresholds pass; set the runtime-ready flag only
after corpus activation and recovery are complete.
Neither fixture was loaded into a memory index or used to run the recall evaluator.
