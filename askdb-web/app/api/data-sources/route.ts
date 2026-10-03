import { proxyDataSourceCatalog } from "@/lib/wren-settings-api";

export const runtime = "nodejs";

export async function GET(request: Request) {
  return proxyDataSourceCatalog(request);
}
