import { redirect } from "next/navigation";

export default function MemoryServiceSettingsRoute() {
  redirect("/settings/memories?tab=service");
}
