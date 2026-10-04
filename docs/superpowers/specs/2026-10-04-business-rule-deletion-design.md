# Business Rule Deletion Lifecycle Design

## Goal

Deleting a conversation that only mentions or uses a business rule must not revoke that shared rule. Deleting the rule's actual source conversation may revoke it, and the UI must show recall and Wren publication state truthfully.

## Current Evidence

- Thread deletion derives affected rules from candidate/origin `source_thread_id` links, not from message text.
- Deletion immediately writes a durable suppression row. Published origins become `removal_pending`; Wren removal is asynchronous.
- The thread deletion status is currently inferred only from pending origin rows. Existing published candidate rows are not immediately changed to revoked/removal-pending.
- Managed Wren rules use the stable name `askdb_br_<business_rule_id>`. A same-label native Wren rule is not sufficient evidence of the same managed rule.

## Design

1. Keep source provenance separate from runtime use. Only a verified candidate origin may be included in thread-deletion impact; runtime recall must never mutate the source link. Explain the relation as “由此会话提交的业务规则” in the confirmation dialog.
2. When deletion revokes a published rule, atomically redact the candidate and mark it `revoked/removal_pending` while retaining its stable ID. Keep the durable suppression row; never clear it to restore recall.
3. Before reporting a Wren removal as complete, reconcile the stable managed rule name and rule file against the active revision. If a suppressed managed rule is still present but its origin row is missing, reconstruct a redacted removal origin from the deletion journal ID and audit hash so the normal durable Wren removal worker can finish it. Never match or remove by display label alone.
4. When the removal revision is activated, mark the candidate `removed` and advance the corresponding thread-deletion operation from `suppressed` to `completed_online` only after every business rule in that deletion event is absent from the active managed Wren revision.

## Compatibility and Safety

- Existing response fields remain available.
- Same-name native Wren rules remain untouched unless they have the managed `askdb_br_<id>` identity.
- If there is no active managed Wren entry for a suppressed ID, no Wren removal operation is needed for that ID; the permanent suppression still prevents recall from an old leased snapshot.
- Existing unrelated working-tree changes must remain intact.

## Acceptance

- A thread with no source link to a rule produces no linked-rule impact and does not suppress it.
- Deleting a true source thread immediately changes an active published candidate to revoked/removal-pending and schedules Wren removal.
- A missing origin plus a still-active `askdb_br_<id>` Wren entry is recovered into the durable removal queue.
- A same-name native Wren rule is not removed by a managed-rule deletion.
- The deletion operation remains `suppressed` while Wren removal is pending and becomes `completed_online` after activation and verification.
- Existing test suite and focused regression tests pass.
