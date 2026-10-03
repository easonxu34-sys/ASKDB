import { DataSourcesPage } from "@/components/wren-settings/data-sources-page";
import { AdminGate } from "@/components/auth/admin-gate";

export default function WrenSettingsPage() {
  return <AdminGate><DataSourcesPage /></AdminGate>;
}
