import { proxyModelSettings } from "@/lib/model-settings-api";

export const runtime = "nodejs";

export async function GET(request: Request) {
  return proxyModelSettings(request, "/v1/settings/model");
}

export async function PUT(request: Request) {
  return proxyModelSettings(request, "/v1/settings/model");
}
