import { csrfTokenFor } from "@/lib/agent-proxy";

export const runtime = "nodejs";

export async function GET() {
  const csrfToken = await csrfTokenFor();
  return Response.json(
    { csrf_token: csrfToken },
    {
      headers: {
        "cache-control": "no-store, max-age=0",
        pragma: "no-cache",
      },
    },
  );
}
