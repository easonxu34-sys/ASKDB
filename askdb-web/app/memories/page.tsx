import { MemoryManagementPage } from "@/components/memory/memory-management-page";
import { SettingsShell } from "@/components/settings/settings-shell";

export default function MyMemorySubmissionsPage() {
  return (
    <SettingsShell>
      <MemoryManagementPage />
    </SettingsShell>
  );
}
