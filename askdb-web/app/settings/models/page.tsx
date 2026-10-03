import { AdminGate } from "@/components/auth/admin-gate";
import { ModelSettingsPage } from "@/components/model-settings/model-settings-page";

export default function ModelSettingsRoute() {
  return (
    <AdminGate>
      <ModelSettingsPage />
    </AdminGate>
  );
}
