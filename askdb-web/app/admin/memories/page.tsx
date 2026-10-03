import { AdminMemoryManagementPageRoute } from "@/components/memory/memory-management-page";
import { SettingsShell } from "@/components/settings/settings-shell";

export default function AdminMemoryManagementRoute() {
  return (
    <SettingsShell>
      <AdminMemoryManagementPageRoute />
    </SettingsShell>
  );
}
