import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useMemo } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";
import { toast } from "sonner";

import { updateGatewayProvider, validateGatewayProvider } from "@/lib/api/tokoboros.functions";
import { useGatewayConfigQuery, useGatewayModelsQuery } from "@/hooks/use-gateway-queries";
import { Skeleton } from "@/components/ui/skeleton";

const providerSchema = z.object({
  providerKind: z.enum(["anthropic", "openai-compatible", "ollama"]),
  model: z.string().min(1, "Model is required"),
  baseUrl: z
    .string()
    .trim()
    .optional()
    .refine(
      (value) => !value || /^https?:\/\//.test(value),
      "Base URL must begin with http:// or https://",
    ),
  apiKeyEnv: z
    .string()
    .trim()
    .optional()
    .refine(
      (value) => !value || /^[A-Z_][A-Z0-9_]*$/.test(value),
      "Use an uppercase env var name such as OPENAI_API_KEY",
    ),
});

type ProviderFormValues = z.infer<typeof providerSchema>;

export function RuntimeConfigScreen() {
  const configQuery = useGatewayConfigQuery();
  const modelsQuery = useGatewayModelsQuery();

  const form = useForm<ProviderFormValues>({
    resolver: zodResolver(providerSchema),
    defaultValues: {
      providerKind: "anthropic",
      model: "",
      baseUrl: "",
      apiKeyEnv: "",
    },
    mode: "onChange",
  });

  useEffect(() => {
    if (!configQuery.data) {
      return;
    }

    form.reset({
      providerKind: configQuery.data.provider.kind,
      model: configQuery.data.provider.model,
      baseUrl: configQuery.data.provider.base_url ?? "",
      apiKeyEnv: configQuery.data.provider.api_key_env ?? "",
    });
  }, [configQuery.data, form]);

  useEffect(() => {
    if (configQuery.error || modelsQuery.error) {
      toast.error("Failed to load runtime config.");
    }
  }, [configQuery.error, modelsQuery.error]);

  const models = useMemo(
    () =>
      (modelsQuery.data ?? []).map((item) => ({
        id: item.id,
        label: item.label,
        provider: item.provider,
      })),
    [modelsQuery.data],
  );

  const filteredModels = useMemo(() => {
    const kind = form.watch("providerKind");
    const subset = models.filter((item) => item.provider === kind);
    return subset.length > 0 ? subset : models;
  }, [form, models]);

  async function save(values: ProviderFormValues) {
    try {
      await updateGatewayProvider({
        data: {
          providerKind: values.providerKind,
          model: values.model,
          baseUrl: values.baseUrl || "",
          apiKeyEnv: values.apiKeyEnv || "",
        },
      });
      toast.success("Runtime provider updated.");
    } catch {
      toast.error("Runtime update failed.");
    }
  }

  async function validateCurrent() {
    const valid = await form.trigger();
    if (!valid) {
      toast.error("Fix validation errors before testing provider config.");
      return;
    }

    const values = form.getValues();
    try {
      const result = await validateGatewayProvider({
        data: {
          providerKind: values.providerKind,
          model: values.model,
          baseUrl: values.baseUrl || "",
          apiKeyEnv: values.apiKeyEnv || "",
        },
      });

      if (result.ok) {
        toast.success("Provider validation passed.");
      } else {
        toast.error(result.errors[0] ?? "Provider validation failed.");
      }

      if (result.warnings.length > 0) {
        toast.warning(result.warnings[0]);
      }
    } catch {
      toast.error("Provider validation request failed.");
    }
  }

  if (configQuery.isPending || modelsQuery.isPending) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  return (
    <form
      onSubmit={form.handleSubmit(save)}
      className="space-y-4 rounded border border-[#304a65] bg-[#081421] p-4"
    >
      <div>
        <div className="text-lg font-semibold text-[#d5e7ff]">Runtime Configuration</div>
        <p className="mt-1 text-xs text-[#94adcd]">
          Update provider defaults and validate connectivity.
        </p>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <label className="text-xs text-[#94adcd]">
          Provider
          <select
            {...form.register("providerKind")}
            className="mt-1 w-full rounded border border-[#304a65] bg-[#070f18] p-2 text-sm text-[#d5e7ff]"
          >
            <option value="anthropic">anthropic</option>
            <option value="openai-compatible">openai-compatible</option>
            <option value="ollama">ollama</option>
          </select>
          {form.formState.errors.providerKind ? (
            <div className="mt-1 text-[11px] text-red-300">
              {form.formState.errors.providerKind.message}
            </div>
          ) : null}
        </label>

        <label className="text-xs text-[#94adcd]">
          Model
          <select
            {...form.register("model")}
            className="mt-1 w-full rounded border border-[#304a65] bg-[#070f18] p-2 text-sm text-[#d5e7ff]"
          >
            {filteredModels.length === 0 ? (
              <option value={form.getValues("model") || ""}>
                {form.getValues("model") || "Select model"}
              </option>
            ) : (
              filteredModels.map((item) => (
                <option value={item.id} key={item.id}>
                  {item.label}
                </option>
              ))
            )}
          </select>
          {form.formState.errors.model ? (
            <div className="mt-1 text-[11px] text-red-300">
              {form.formState.errors.model.message}
            </div>
          ) : null}
        </label>

        <label className="text-xs text-[#94adcd]">
          Base URL (optional)
          <input
            type="text"
            {...form.register("baseUrl")}
            placeholder="https://api.openai.com/v1"
            className="mt-1 w-full rounded border border-[#304a65] bg-[#070f18] p-2 text-sm text-[#d5e7ff]"
          />
          {form.formState.errors.baseUrl ? (
            <div className="mt-1 text-[11px] text-red-300">
              {form.formState.errors.baseUrl.message}
            </div>
          ) : null}
        </label>

        <label className="text-xs text-[#94adcd]">
          API key env var (optional)
          <input
            type="text"
            {...form.register("apiKeyEnv")}
            placeholder="OPENAI_API_KEY"
            className="mt-1 w-full rounded border border-[#304a65] bg-[#070f18] p-2 text-sm text-[#d5e7ff]"
          />
          {form.formState.errors.apiKeyEnv ? (
            <div className="mt-1 text-[11px] text-red-300">
              {form.formState.errors.apiKeyEnv.message}
            </div>
          ) : null}
        </label>
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={validateCurrent}
          disabled={form.formState.isSubmitting}
          className="rounded border border-[#4fdbc8]/40 bg-[#4fdbc8]/10 px-4 py-2 text-xs font-semibold text-[#4fdbc8] disabled:opacity-40"
        >
          Validate provider
        </button>
        <button
          type="submit"
          disabled={form.formState.isSubmitting}
          className="rounded bg-[#4fdbc8] px-4 py-2 text-xs font-semibold text-[#00161e] disabled:opacity-40"
        >
          {form.formState.isSubmitting ? "Saving..." : "Save runtime config"}
        </button>
      </div>
    </form>
  );
}
