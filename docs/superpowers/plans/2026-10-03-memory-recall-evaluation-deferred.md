# Memory Recall Gold Review and Scoring — Deferred

**Recorded:** 2026-10-03
**Decision:** Deferred by the user until the memory system is fully assembled; the user plans to run end-to-end testing then. This work is postponed, not cancelled.

## Deferred work

1. Repair the six `[已脱敏记录标识]` fragments found in schema document bodies in `askdb_tpcc_recall_gold_draft.json`; regenerate or correct them from the compiled MDL and recheck the affected documents.
2. Human-review all 52 cases and their expected/prohibited document IDs:
   - 22 query-example cases: confirm the question, target example, SQL template, model/field names, and typed parameters agree.
   - 20 schema cases: confirm each question targets the marked model/field and the document text matches the compiled MDL.
   - 8 no-match cases: confirm no document in the target source should answer them.
   - 2 source-isolation cases: confirm the target-source document is expected and the other source's document is prohibited.
3. Record each human decision, corrected labels, rationale, reviewer, and review date. The CSV's external `APPROVED` value is provenance, not acceptance of this recall gold set.
4. Add a small offline JSON loader/report command around `evaluate_lexical_recall()`. The evaluator function exists, but there is no fixture-loading CLI in `askdb-agent/evals/memory/` yet.
5. Run the offline recall score after review. The current release gate requires at least 20 `schema_rule`, 20 `query_example`, and 10 `no_match_or_isolation` cases; schema Hit@5 >= 80%; query-example Hit@3 >= 80%; and zero no-match false recalls and zero prohibited-document recalls.

## Scope and safety

- This fixture remains an offline draft, not an application/Wren memory corpus. Keep its placeholder source/revision bindings self-consistent for offline scoring; do not treat scoring as corpus activation.
- Recall acceptance and runtime readiness are separate gates. Do not set `ASKDB_AGENT_RECALL_GOLD_ACCEPTED=1` before human review and a passing score, or `ASKDB_AGENT_RECALL_RUNTIME_READY=1` before activation/recovery acceptance. Online Recall stays disabled until both gates and the memory/journal prerequisites are satisfied.
- This evaluation measures retrieval selection and source isolation. It does not establish that SQL returns correct rows. The fixture omits sample parameter values and result evidence; validating SQL results would require a separate, explicitly authorized read-only check or reviewer-provided evidence.
- Resume this work when the user says the memory system is assembled and is ready for self-testing. First repair the draft and prepare a review sheet, then complete human sign-off, add/run the offline loader, and report the gate metrics before changing any runtime flags.

## Current status

`askdb_tpcc_recall_gold_draft.json` remains `draft_requires_review_and_scoring`. No human approval or score has been recorded. See the [memory implementation progress ledger](2026-10-02-agent-memory-progress.md) for the rest of the project status.
