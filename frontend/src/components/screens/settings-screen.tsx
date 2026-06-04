import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { toast } from "sonner";

import { updateGatewayMemory, updateGatewayPermissions } from "@/lib/api/tokoboros.functions";
import { useGatewayConfigQuery } from "@/hooks/use-gateway-queries";
import { Skeleton } from "@/components/ui/skeleton";

const settingsSchema = z.object({
  tier: z.enum(["sandbox", "guided", "unrestricted"]),
  enableSessionHistory: z.boolean(),
  enableSemanticMemory: z.boolean(),
});

type SettingsFormValues = z.infer<typeof settingsSchema>;

export function SettingsScreen() {
  const configQuery = useGatewayConfigQuery();

  const form = useForm<SettingsFormValues>({
    resolver: zodResolver(settingsSchema),
    defaultValues: {
      tier: "guided",
      enableSessionHistory: true,
      enableSemanticMemory: true,
    },
  });

  useEffect(() => {
    if (!configQuery.data) {
      return;
    }

    form.reset({
      tier: configQuery.data.permissions.tier,
      enableSessionHistory: configQuery.data.memory.enable_session_history,
      enableSemanticMemory: configQuery.data.memory.enable_semantic_memory,
    });
  }, [configQuery.data, form]);

  useEffect(() => {
    if (configQuery.error) {
      toast.error("Failed to load settings.");
    }
  }, [configQuery.error]);

  async function save(values: SettingsFormValues) {
    try {
      await Promise.all([
        updateGatewayPermissions({ data: { tier: values.tier } }),
        updateGatewayMemory({
          data: {
            enableSessionHistory: values.enableSessionHistory,
            enableSemanticMemory: values.enableSemanticMemory,
          },
        }),
      ]);
      toast.success("Settings saved.");
    } catch {
      toast.error("Settings update failed.");
    }
  }

  if (configQuery.isPending) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-44" />
        <Skeleton className="h-52 w-full" />
      </div>
    );
  }

  return (
    <form
      onSubmit={form.handleSubmit(save)}
      className="rounded border border-[#304a65] bg-[#081421] p-4"
    >
      <div className="text-lg font-semibold text-[#d5e7ff]">Settings</div>
      <div className="mt-4 grid gap-4 md:grid-cols-3">
        <label className="text-xs text-[#94adcd]">
          Permission tier
          <select
            {...form.register("tier")}
            className="mt-1 w-full rounded border border-[#304a65] bg-[#070f18] p-2 text-sm text-[#d5e7ff]"
          >
            <option value="sandbox">sandbox</option>
            <option value="guided">guided</option>
            <option value="unrestricted">unrestricted</option>
          </select>
          {form.formState.errors.tier ? (
            <div className="mt-1 text-[11px] text-red-300">
              {form.formState.errors.tier.message}
            </div>
          ) : null}
        </label>

        <label className="flex items-center gap-2 rounded border border-[#304a65] bg-[#070f18] p-2 text-sm text-[#d5e7ff]">
          <input type="checkbox" {...form.register("enableSessionHistory")} />
          Enable session history
        </label>

        <label className="flex items-center gap-2 rounded border border-[#304a65] bg-[#070f18] p-2 text-sm text-[#d5e7ff]">
          <input type="checkbox" {...form.register("enableSemanticMemory")} />
          Enable semantic memory
        </label>
      </div>

      <button
        type="submit"
        disabled={form.formState.isSubmitting}
        className="mt-4 rounded bg-[#4fdbc8] px-4 py-2 text-xs font-semibold text-[#00161e] disabled:opacity-40"
      >
        {form.formState.isSubmitting ? "Saving..." : "Save settings"}
      </button>
    </form>
  );
}
