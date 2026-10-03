import { proxyModelSettings } from "@/lib/model-settings-api";

export const runtime = "nodejs";

export async function POST(request: Request) {
  return proxyModelSettings(request, "/v1/settings/model/test");
}
