import { proxyModelSettings } from "@/lib/model-settings-api";
export const runtime = "nodejs";
export async function PUT(
  request: Request,
  { params }: { params: Promise<{ profileId: string }> },
) {
  const { profileId } = await params;
  return proxyModelSettings(
    request,
    `/v1/settings/models/${encodeURIComponent(profileId)}/memory-processing`,
  );
}
