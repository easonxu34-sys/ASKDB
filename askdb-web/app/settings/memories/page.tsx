import { AdminMemoryManagementPageRoute } from "@/components/memory/memory-management-page";

export default async function SettingsMemoryManagementPage({
  searchParams,
}: {
  searchParams: Promise<{ tab?: string | string[] }>;
}) {
  const params = await searchParams;
  const tab = Array.isArray(params.tab) ? params.tab[0] : params.tab;
  const initialTab = tab === "business-rules" || tab === "service" ? tab : "query-examples";
  return <AdminMemoryManagementPageRoute initialTab={initialTab} syncTabToUrl />;
}
