import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import { test } from "node:test";
import { pathToFileURL } from "node:url";

const authApiUrl = pathToFileURL(new URL("../lib/auth-api.ts", import.meta.url).pathname).href;
registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "@/lib/auth-api") return { url: authApiUrl, shortCircuit: true };
    return nextResolve(specifier, context);
  },
});

const { effectivePermissionAgentPath, isEffectivePermissionPage } =
  await import("../lib/effective-permissions.ts");

function validPage() {
  return {
    read_at: "2026-10-10T08:00:00+00:00",
    target: {
      type: "user",
      id: "user-1",
      username: "alice",
      role: "member",
      is_active: true,
    },
    counts: {
      total: 1,
      usable: 1,
      granted_blocked: 0,
      ungranted: 0,
      multiple_sources: 0,
      matching: 1,
    },
    items: [
      {
        user: { id: "user-1", username: "alice", role: "member", is_active: true },
        data_source: {
          id: "source-1",
          display_name: "Finance",
          exists: true,
          enabled: true,
          runtime_status: "ready",
        },
        grant_sources: [
          {
            type: "direct",
            key: "direct",
            granted_by: "admin-1",
            granted_at: "2026-10-01T00:00:00+00:00",
            role_id: null,
            role_name: null,
            bound_by: null,
            bound_at: null,
          },
        ],
        has_grant: true,
        access_allowed: true,
        runtime_ready: true,
        usable_now: true,
        block_reasons: [],
        removal_impacts: [
          {
            source_key: "direct",
            remaining_sources: [],
            access_allowed_after: false,
            would_lose_access: true,
          },
        ],
      },
    ],
    next_cursor: null,
  };
}

test("accepts a valid permission page and rejects unknown or malformed sources", () => {
  assert.equal(isEffectivePermissionPage(validPage()), true);

  const unknownType = validPage();
  unknownType.items[0].grant_sources[0].type = "inherited";
  assert.equal(isEffectivePermissionPage(unknownType), false);

  const invalidTime = validPage();
  invalidTime.items[0].grant_sources[0].granted_at = "not-a-date";
  assert.equal(isEffectivePermissionPage(invalidTime), false);

  const missingSourceId = validPage();
  delete missingSourceId.items[0].data_source.id;
  assert.equal(isEffectivePermissionPage(missingSourceId), false);
});

test("rejects oversized pages and only forwards validated query parameters", () => {
  const oversized = validPage();
  oversized.items = Array.from({ length: 101 }, () => structuredClone(oversized.items[0]));
  assert.equal(isEffectivePermissionPage(oversized), false);

  assert.equal(
    effectivePermissionAgentPath(
      new URL(
        "https://askdb.local/api/admin/effective-permissions?view=user&user_id=user-1&limit=20",
      ),
    ),
    "/v1/admin/effective-permissions?view=user&user_id=user-1&limit=20",
  );
  assert.equal(
    effectivePermissionAgentPath(
      new URL("https://askdb.local/api/admin/effective-permissions/export?view=all"),
      true,
    ),
    "/v1/admin/effective-permissions/export?view=all",
  );
  assert.throws(
    () =>
      effectivePermissionAgentPath(
        new URL(
          "https://askdb.local/api/admin/effective-permissions?view=user&user_id=user-1&token=secret",
        ),
      ),
    /query parameter is invalid/,
  );
  assert.throws(
    () =>
      effectivePermissionAgentPath(
        new URL(
          "https://askdb.local/api/admin/effective-permissions?view=user&view=source&user_id=user-1",
        ),
      ),
    /query parameter is invalid/,
  );
});
