import { proxyAgentJson, requireAgentSession } from "@/lib/agent-proxy";
import {
  CHART_EDIT_BODY_LIMIT, CHART_EDIT_REQUEST_KEYS, sanitizeChartEditRequest,
  readChartEditIntent, readChartEditError,
} from "@/lib/chart-edit-request.mjs";

const NO_STORE = { "cache-control": "no-store", "content-type": "application/json; charset=utf-8" };
function error(code: string, status: number) {
  return Response.json(readChartEditError({ code }), { status, headers: NO_STORE });
}

function responseValidationDiagnostic(cause: unknown) {
  const diagnostic = {
    diagnostic_id: crypto.randomUUID().replaceAll("-", ""),
    stage: "bff_response_validation",
    exception_type: cause instanceof TypeError ? "TypeError" : "Error",
    cause_types: [],
    provider_status_code: null,
    provider_output_chars: null,
    validation_issues: [],
  };
  console.error("chart_edit_failure=" + JSON.stringify({
    event: "chart_edit_failure",
    code: "AGENT_RESPONSE_INVALID",
    diagnostic,
  }));
  return diagnostic;
}

export async function POST(request: Request): Promise<Response> {
  const access = await requireAgentSession(request, true);
  if ("response" in access) return access.response;
  const declared = request.headers.get("content-length");
  if (declared && (!/^\d+$/.test(declared) || Number(declared) > CHART_EDIT_BODY_LIMIT)) {
    return error("CHART_EDIT_REQUEST_TOO_LARGE", 413);
  }
  let body: unknown;
  try {
    if (!request.body) return error("CHART_EDIT_INPUT_INVALID", 400);
    const reader = request.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > CHART_EDIT_BODY_LIMIT) {
        try { await reader.cancel(); } catch { /* Preserve the bounded response. */ }
        return error("CHART_EDIT_REQUEST_TOO_LARGE", 413);
      }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    body = sanitizeChartEditRequest(JSON.parse(new TextDecoder("utf-8", { fatal: true }).decode(bytes)));
  } catch { return error("CHART_EDIT_INPUT_INVALID", 400); }

  try {
    const upstream = await proxyAgentJson(request, "/v1/chart-edits/interpret", {
      method: "POST", body, bodyLimit: CHART_EDIT_BODY_LIMIT, allowedBodyKeys: CHART_EDIT_REQUEST_KEYS,
    });
    const payload: unknown = await upstream.json();
    if (!upstream.ok) {
      return Response.json(readChartEditError(payload), { status: upstream.status, headers: NO_STORE });
    }
    try {
      return Response.json(readChartEditIntent(payload), { status: 200, headers: NO_STORE });
    } catch (cause) {
      const diagnostic = responseValidationDiagnostic(cause);
      return Response.json(
        readChartEditError({ code: "AGENT_RESPONSE_INVALID", diagnostic }),
        { status: 502, headers: NO_STORE },
      );
    }
  } catch { return error("AGENT_UNAVAILABLE", 503); }
}
