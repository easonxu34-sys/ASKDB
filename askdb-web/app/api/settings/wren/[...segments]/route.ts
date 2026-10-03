import { proxyWrenSettings } from "@/lib/wren-settings-api";

export const runtime = "nodejs";

type RouteContext = { params: Promise<{ segments: string[] }> };

async function proxy(request: Request, context: RouteContext) {
  const { segments } = await context.params;
  return proxyWrenSettings(request, segments);
}

export const GET = proxy;
export const POST = proxy;
export const PUT = proxy;
