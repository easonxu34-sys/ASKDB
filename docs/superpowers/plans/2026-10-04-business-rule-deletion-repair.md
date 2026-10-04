# Business Rule Deletion Lifecycle Implementation Plan

> **For agentic workers:** Inline execution is authorized by the user. Preserve all existing uncommitted changes. Steps use checkbox syntax for tracking.

**Goal:** Keep shared business rules active when deleting non-source conversations and make source-session revocation plus Wren removal recoverable and accurately reported.

**Architecture:** Preserve stable business-rule IDs across conversation suppression and Wren publication. Mark candidate state immediately, reconstruct only verifiable orphaned managed-rule removals, and update deletion-operation status after active-revision verification. The UI describes a source relationship instead of a vague reference.

**Tech Stack:** Python 3, SQLite, FastAPI, React/TypeScript, pytest, existing Wren revision publisher.

**Spec:** `docs/superpowers/specs/2026-10-04-business-rule-deletion-design.md`

## Global Constraints

- Never remove a managed Wren rule by display label; use `askdb_br_<business_rule_id>`.
- Keep the encrypted deletion journal and suppression ledger append-only.
- Do not read, change, stage, or commit `askdb-server/askdb-admin/src/main/resources/application.yml`.
- Do not overwrite or stage pre-existing user changes.

---

### Task 1: Revoke linked published candidates immediately

**Files:**
- Modify: `askdb-agent/src/integrations/business_rule_store.py`
- Test: `askdb-agent/tests/test_business_rule_deletion.py`

- [ ] Add a failing test for deleting a source thread linked to an active published origin; assert candidate becomes `revoked/removal_pending` and rule text is redacted.
- [ ] Run the focused test and confirm it fails because the current code only updates the origin.
- [ ] Update the candidate row and append an audit event in the thread-delete participant transaction.
- [ ] Run the focused test and confirm it passes.

### Task 2: Recover verifiable orphaned Wren removals

**Files:**
- Modify: `askdb-agent/src/integrations/business_rule_store.py`
- Test: `askdb-agent/tests/test_business_rule_deletion.py`

- [ ] Add a failing test with a durable thread-delete suppression, missing origin, and an active `askdb_br_<id>` rule; assert `list_pending_removals()` reconstructs the redacted origin.
- [ ] Run the focused test and confirm it fails because current recovery requires a candidate row.
- [ ] Recover only when the stable managed rule ID is verifiably present in the active Wren revision config or its rule file; never use the display label.
- [ ] Add a passing test that an unrelated native Wren rule with the same label is not considered a managed match.
- [ ] Run the focused tests and confirm they pass.

### Task 3: Complete deletion state after Wren activation

**Files:**
- Modify: `askdb-agent/src/integrations/business_rule_store.py`
- Test: `askdb-agent/tests/test_business_rule_deletion.py`

- [ ] Add a failing test asserting a thread deletion stays `suppressed` while any event-linked rule is `removal_pending`.
- [ ] Add a failing test asserting `mark_removed()` changes the operation to `completed_online` only after all rules in that event are no longer pending.
- [ ] Implement the event-scoped status transition and run the focused tests.

### Task 4: Explain the actual deletion relationship in the UI

**Files:**
- Modify: `askdb-web/components/threads/thread-delete-confirmation-dialog.tsx`

- [ ] Update the copy to say the listed rules were submitted from this conversation and will be revoked; preserve the existing uncommitted edit in this file.
- [ ] Review the diff to ensure no unrelated UI changes were lost.

### Task 5: Verify and report

**Files:** none

- [ ] Run the focused Python regression file.
- [ ] Run the Agent Python test suite and relevant Web typecheck only if the focused changes pass.
- [ ] Run `git diff --check` and inspect `git status` to confirm pre-existing edits remain untouched.
