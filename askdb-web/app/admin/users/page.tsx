import { UserManagement } from "@/components/user-management/user-management";
import { SettingsShell } from "@/components/settings/settings-shell";

export default function AdminUsersPage() {
  return (
    <SettingsShell>
      <UserManagement />
    </SettingsShell>
  );
}
