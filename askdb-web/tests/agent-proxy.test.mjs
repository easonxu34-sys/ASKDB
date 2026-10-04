import assert from "node:assert/strict";
import { registerHooks } from "node:module";
import test from "node:test";

// URL validation does not use Next's request-scoped cookies. Keep that dependency
// out of this standalone Node test, and fail if the URL helper ever calls it.
const hooks = registerHooks({
  resolve(specifier, context, nextResolve) {
    if (specifier === "next/headers") {
      return {
        url: "data:text/javascript,export function cookies() { throw new Error('Unexpected cookie access'); }",
        shortCircuit: true,
      };
    }
    return nextResolve(specifier, context);
  },
});
const { agentUrl } = await import("../lib/agent-proxy.ts");
hooks.deregister();

function withConfig(base, hosts, run) {
  const previousBase = process.env.ASKDB_AGENT_URL;
  const previousHosts = process.env.ASKDB_AGENT_INTERNAL_HTTP_HOSTS;
  try {
    if (base === undefined) delete process.env.ASKDB_AGENT_URL;
    else process.env.ASKDB_AGENT_URL = base;
    if (hosts === undefined) delete process.env.ASKDB_AGENT_INTERNAL_HTTP_HOSTS;
    else process.env.ASKDB_AGENT_INTERNAL_HTTP_HOSTS = hosts;
    run();
  } finally {
    if (previousBase === undefined) delete process.env.ASKDB_AGENT_URL;
    else process.env.ASKDB_AGENT_URL = previousBase;
    if (previousHosts === undefined) delete process.env.ASKDB_AGENT_INTERNAL_HTTP_HOSTS;
    else process.env.ASKDB_AGENT_INTERNAL_HTTP_HOSTS = previousHosts;
  }
}

test("rejects non-loopback HTTP when the allowlist is unset or empty", () => {
  for (const hosts of [undefined, "", " , , "]) {
    withConfig("http://agent:8000", hosts, () => assert.throws(() => agentUrl("/healthz")));
  }
});

test("accepts exact hostnames from a comma-separated case-insensitive allowlist", () => {
  withConfig("http://AgEnT:8000/", " other, AGENT , , worker ", () => {
    assert.equal(agentUrl("/healthz"), "http://AgEnT:8000/healthz");
  });
  withConfig("http://worker:8000", " other, AGENT , , worker ", () => {
    assert.equal(agentUrl("/healthz"), "http://worker:8000/healthz");
  });
});

test("rejects suffixes, prefixes, subdomains, and lookalike hostnames", () => {
  for (const base of ["http://agent.evil.test:8000", "http://evilagent:8000", "http://sub.agent:8000", "http://agent-evil:8000", "http://agent.:8000"]) {
    withConfig(base, "agent", () => assert.throws(() => agentUrl("/healthz")));
  }
});

test("preserves loopback HTTP and the default URL without an allowlist", () => {
  for (const base of ["http://localhost:8000", "http://127.0.0.1:8000", "http://127.12.34.56:8000", "http://[::1]:8000"]) {
    withConfig(base, undefined, () => assert.equal(agentUrl("/healthz"), `${base}/healthz`));
  }
  withConfig(undefined, undefined, () => assert.equal(agentUrl("/healthz"), "http://127.0.0.1:8000/healthz"));
});

test("preserves HTTPS for non-loopback hosts without an allowlist", () => {
  withConfig("https://example.test", undefined, () => assert.equal(agentUrl("/healthz"), "https://example.test/healthz"));
});

test("rejects URL credentials even for an allowed host or HTTPS", () => {
  for (const base of ["http://user@agent:8000", "http://:secret@agent:8000", "https://user:secret@example.test"]) {
    withConfig(base, "agent,example.test", () => assert.throws(() => agentUrl("/healthz")));
  }
});

test("rejects unsupported schemes even for an allowed host", () => {
  for (const base of ["ftp://agent:8000", "ws://agent:8000", "file://agent/tmp"]) {
    withConfig(base, "agent", () => assert.throws(() => agentUrl("/healthz")));
  }
});
