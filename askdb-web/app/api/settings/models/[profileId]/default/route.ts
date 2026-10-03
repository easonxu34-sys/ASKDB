import { proxyModelSettings } from "@/lib/model-settings-api";

export const runtime = "nodejs";

type RouteContext = { params: Promise<{ profileId: string }> };

export async function PUT(request: Request, { params }: RouteContext) {
  const { profileId } = await params;
  return proxyModelSettings(
    request,
    `/v1/settings/models/${encodeURIComponent(profileId)}/default`,
  );
}
