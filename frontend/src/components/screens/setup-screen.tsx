import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  applyGatewaySetup,
  getGatewayModels,
  getGatewayReadiness,
  getGatewaySetupState,
} from "@/lib/api/tokoboros.functions";
import { Skeleton } from "@/components/ui/skeleton";

const PROVIDER_DEFAULT_MODELS = {
  anthropic: "claude-haiku-4-5",
  "openai-compatible": "gpt-4o-mini",
  openrouter: "openai/gpt-4o-mini",
  ollama: "llama3.1",
} as const;

export function SetupScreen() {
  const [loading, setLoading] = useState(true);
  const [ready, setReady] = useState(false);
  const [needsSetup, setNeedsSetup] = useState(true);
  const [tier, setTier] = useState<"lightning" | "recommended" | "enhanced" | "power">(
    "recommended",
  );
  const [providerKind, setProviderKind] = useState<
    "anthropic" | "openai-compatible" | "openrouter" | "ollama"
  >("anthropic");
  const [model, setModel] = useState("claude-haiku-4-5");
  const [modelOptions, setModelOptions] = useState<string[]>([]);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    let mounted = true;
    void (async () => {
      try {
        const [readiness, setup, models] = await Promise.all([
          getGatewayReadiness(),
          getGatewaySetupState(),
          getGatewayModels(),
        ]);
        if (!mounted) return;
        setReady(readiness.ready);
        setNeedsSetup(setup.needs_setup);
        const labels = models.map((item) => item.id);
        setModelOptions(labels);
        if (labels.length > 0) {
          setModel(labels[0]);
        }
      } catch {
        if (!mounted) return;
        toast.error("Failed to load setup state.");
      } finally {
        if (mounted) {
          setLoading(false);
        }
      }
    })();
    return () => {
      mounted = false;
    };
  }, []);

  async function submitSetup() {
    if (submitting || !ready) {
      return;
    }

    const payload = { tier, providerKind, model };
    setSubmitting(true);
    try {
      await applyGatewaySetup({ data: payload });
      const [readiness, setup] = await Promise.all([getGatewayReadiness(), getGatewaySetupState()]);
      setReady(readiness.ready);
      setNeedsSetup(setup.needs_setup);
      if (setup.needs_setup) {
        toast.warning("Setup submitted but gateway still reports setup required.");
      } else {
        toast.success("Setup saved.");
      }
    } catch {
      toast.error("Setup apply failed.");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-56 w-full" />
      </div>
    );
  }

  if (ready && !needsSetup) {
    return (
      <div className="rounded border border-emerald-400/30 bg-emerald-500/5 p-4 text-emerald-100">
        Setup already complete. The gateway is ready.
      </div>
    );
  }

  return (
    <div className="rounded border border-[#304a65] bg-[#081421] p-4">
      <div className="text-lg font-semibold text-[#d5e7ff]">Setup</div>
      <div className="mt-1 text-xs text-[#94adcd]">
        Configure provider, model, and baseline tier.
      </div>

      <div className="mt-4 grid gap-3 md:grid-cols-3">
        <label className="text-xs text-[#94adcd]">
          Tier
          <select
            value={tier}
            onChange={(event) => setTier(event.target.value as typeof tier)}
            disabled={submitting}
            className="mt-1 w-full rounded border border-[#304a65] bg-[#070f18] p-2 text-sm text-[#d5e7ff]"
          >
            <option value="lightning">lightning</option>
            <option value="recommended">recommended</option>
            <option value="enhanced">enhanced</option>
            <option value="power">power</option>
          </select>
        </label>

        <label className="text-xs text-[#94adcd]">
          Provider
          <select
            value={providerKind}
            onChange={(event) => {
              const nextKind = event.target.value as typeof providerKind;
              setProviderKind(nextKind);
              setModel(PROVIDER_DEFAULT_MODELS[nextKind]);
            }}
            disabled={submitting}
            className="mt-1 w-full rounded border border-[#304a65] bg-[#070f18] p-2 text-sm text-[#d5e7ff]"
          >
            <option value="anthropic">anthropic</option>
            <option value="openai-compatible">openai-compatible</option>
            <option value="openrouter">openrouter</option>
            <option value="ollama">ollama</option>
          </select>
        </label>

        <label className="text-xs text-[#94adcd]">
          Model
          <select
            value={model}
            onChange={(event) => setModel(event.target.value)}
            disabled={submitting}
            className="mt-1 w-full rounded border border-[#304a65] bg-[#070f18] p-2 text-sm text-[#d5e7ff]"
          >
            {modelOptions.length === 0 ? <option value={model}>{model}</option> : null}
            {modelOptions.map((option) => (
              <option value={option} key={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
      </div>

      <button
        type="button"
        onClick={submitSetup}
        disabled={submitting || !ready}
        className="mt-4 rounded bg-[#4fdbc8] px-4 py-2 text-xs font-semibold text-[#00161e] disabled:opacity-40"
      >
        {submitting ? "Saving..." : "Apply setup"}
      </button>

      {!ready ? (
        <div className="mt-3 text-xs text-amber-300">
          Gateway readiness is offline. Setup apply is disabled until readiness recovers.
        </div>
      ) : null}
    </div>
  );
}
