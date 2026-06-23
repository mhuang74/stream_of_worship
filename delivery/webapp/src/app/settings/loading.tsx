import { SettingsSkeleton } from "@/components/settings/SettingsSkeleton";

export default function SettingsLoading() {
  return (
    <div className="px-4 py-6 max-w-2xl mx-auto">
      <h1 className="text-2xl font-bold mb-6">Settings</h1>
      <SettingsSkeleton />
    </div>
  );
}
