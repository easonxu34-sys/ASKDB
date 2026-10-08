# Memory Recall Gold Review and Scoring — Deferred

**Recorded:** 2026-10-03
**Decision:** Deferred by the user until the memory system is fully assembled; the user plans to run end-to-end testing then. This work is postponed, not cancelled.

**Update (2026-10-04):** The user resumed this work and asked to follow the
listed order. The six descriptions are repaired, an offline loader/report
command exists, and a draft diagnostic report has been generated. Human review
was then explicitly confirmed for all 52 cases by the user; the accepted offline
score is recorded separately below. Runtime readiness and the online recall gate
remain separate.

**Update (2026-10-04, later decision):** The user will assess recall quality
themselves and does not want the offline gold score or runtime-ready environment
flag to gate startup. Persistent memory and online recall now start by default;
the Agent creates a stable journal key on first startup. `ASKDB_AGENT_RECALL_ENABLED=0`
is an explicit recall opt-out. This does not activate unpublished query examples
or replace corpus binding, review, SQL validation, and publication.

## Deferred work

1. **Complete:** repaired the six fragments by regenerating the ten MDL-backed model documents from `askdb-project/target/mdl.json`; terms and content hashes were regenerated too.
2. **Complete:** the user confirmed all 52 expected/prohibited label decisions on 2026-10-04 using `askdb_tpcc_recall_review_sheet.csv`:
   - 22 query-example cases: confirm the question, target example, SQL template, model/field names, and typed parameters agree.
   - 20 schema cases: confirm each question targets the marked model/field and the document text matches the compiled MDL.
   - 8 no-match cases: confirm no document in the target source should answer them.
   - 2 source-isolation cases: confirm the target-source document is expected and the other source's document is prohibited.
3. **Complete:** recorded approved decisions, rationale, reviewer provenance, review date, and the full reviewed case ID list in the fixture and worksheet. The CSV's original external `APPROVED` value remains provenance, not this review decision.
4. **Complete:** added `evals/memory/run_recall_eval.py`, which loads JSON, validates counts/references/placeholders, and emits text or JSON with per-category metrics and per-case results.
5. **Complete:** accepted evaluation report at `evals/memory/reports/2026-10-04-approved-recall-report.json`. The release gate requires at least 20 `schema_rule`, 20 `query_example`, and 10 `no_match_or_isolation` cases; schema Hit@5 >= 80%; query-example Hit@3 >= 80%; and zero no-match false recalls and zero prohibited-document recalls. The reviewed set meets these conditions.

## Scope and safety

- This fixture remains an offline draft, not an application/Wren memory corpus. Keep its placeholder source/revision bindings self-consistent for offline scoring; do not treat scoring as corpus activation.
- The offline gold score and runtime activation/recovery remain distinct evidence, but the user chose not to make either an environment-flag startup gate. The Agent now creates persistent journal storage automatically when it is absent.
- This evaluation measures retrieval selection and source isolation. It does not establish that SQL returns correct rows. The fixture omits sample parameter values and result evidence; validating SQL results would require a separate, explicitly authorized read-only check or reviewer-provided evidence.
- The reviewed offline gold score is complete but optional for startup. Memory and recall default to on; active corpus binding, review, SQL validation, publication, and recovery behavior still govern which material can be recalled safely.

## Current status

`askdb_tpcc_recall_gold_draft.json` records user-approved labels and a passing offline retrieval score, which remains optional for startup. Online recall defaults on when persistent memory and its journal are configured. See the [memory implementation progress ledger](2026-10-02-agent-memory-progress.md) for the rest of the project status.
