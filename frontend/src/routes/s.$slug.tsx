import { createFileRoute, Link, notFound, redirect } from "@tanstack/react-router";

import { LogsScreen } from "@/components/screens/logs-screen";
import { RuntimeConfigScreen } from "@/components/screens/runtime-config-screen";
import { SessionWorkbench } from "@/components/screens/session-workbench";
import { SettingsScreen } from "@/components/screens/settings-screen";
import { SetupScreen } from "@/components/screens/setup-screen";
import { getGatewayReadiness, getGatewaySetupState } from "@/lib/api/tokoboros.functions";

const map: Record<string, { id: number; title: string; kind: "react" | "static" }> = {
  main: { id: 2, title: "Main Console", kind: "react" },
  operator: { id: 15, title: "Operator Console", kind: "react" },
  console: { id: 14, title: "Console", kind: "react" },
  agents: { id: 10, title: "Agents Inventory", kind: "static" },
  logs: { id: 12, title: "System Logs & Telemetry", kind: "react" },
  runtime: { id: 3, title: "Runtime Configuration", kind: "react" },
  settings: { id: 4, title: "Settings Panel", kind: "react" },
  setup: { id: 5, title: "System Setup", kind: "react" },
  "setup-2": { id: 8, title: "System Setup II", kind: "react" },
  models: { id: 7, title: "Model Selection", kind: "static" },
  deployment: { id: 9, title: "Deployment Success", kind: "static" },
  "tool-output": { id: 6, title: "Tool Output Block", kind: "static" },
  "input-states": { id: 13, title: "Input Area States", kind: "static" },
  error: { id: 11, title: "Configuration Error", kind: "static" },
};

export const Route = createFileRoute("/s/$slug")({
  loader: async ({ params }) => {
    const entry = map[params.slug];
    if (!entry) throw notFound();

    const guardedSlugs = new Set(["main", "operator", "console", "logs", "runtime", "settings"]);
    if (guardedSlugs.has(params.slug)) {
      let readiness;
      let setup;
      try {
        [readiness, setup] = await Promise.all([getGatewayReadiness(), getGatewaySetupState()]);
      } catch {
        throw redirect({ to: "/s/$slug", params: { slug: "error" } });
      }

      if (setup.needs_setup) {
        throw redirect({ to: "/s/$slug", params: { slug: "setup" } });
      }
      if (!readiness.ready) {
        throw redirect({ to: "/s/$slug", params: { slug: "error" } });
      }
    }

    return entry;
  },
  head: ({ loaderData }) => ({
    meta: [
      { title: `${loaderData?.title ?? "Screen"} — Tokoboros` },
      {
        name: "description",
        content: `Tokoboros operator workbench — ${loaderData?.title ?? "screen"}.`,
      },
    ],
  }),
  component: ScreenView,
  notFoundComponent: () => (
    <div className="min-h-screen bg-[#070f18] text-[#d5e7ff] flex items-center justify-center font-mono">
      <div className="text-center">
        <div className="text-[10px] uppercase tracking-widest text-[#94adcd]">404</div>
        <h1 className="text-2xl font-black text-[#4fdbc8] mt-2">Screen not found</h1>
        <Link to="/" className="text-xs text-[#94adcd] hover:text-[#4fdbc8] mt-4 inline-block">
          ← Back to index
        </Link>
      </div>
    </div>
  ),
});

function ScreenView() {
  const { slug } = Route.useParams();
  const entry = Route.useLoaderData();

  const body = (() => {
    if (entry.kind === "static") {
      return (
        <iframe
          src={`/screens/${entry.id}.html`}
          title={entry.title}
          className="flex-1 w-full border-0 bg-[#070f18]"
        />
      );
    }

    if (slug === "settings") {
      return (
        <div className="flex-1 overflow-auto px-4 py-4">
          <SettingsScreen />
        </div>
      );
    }

    if (slug === "runtime") {
      return (
        <div className="flex-1 overflow-auto px-4 py-4">
          <RuntimeConfigScreen />
        </div>
      );
    }

    if (slug === "setup" || slug === "setup-2") {
      return (
        <div className="flex-1 overflow-auto px-4 py-4">
          <SetupScreen />
        </div>
      );
    }

    if (slug === "logs") {
      return (
        <div className="flex-1 overflow-auto px-4 py-4">
          <LogsScreen />
        </div>
      );
    }

    return (
      <div className="flex-1 overflow-auto px-4 py-4">
        <SessionWorkbench
          surface={slug === "operator" ? "operator" : slug === "console" ? "console" : "main"}
        />
      </div>
    );
  })();

  return (
    <div className="h-screen w-screen flex flex-col bg-[#070f18]">
      <div className="flex items-center justify-between px-4 py-2 border-b border-[#304a65] bg-[#0a1b2b] shrink-0 font-mono">
        <Link
          to="/"
          className="text-[10px] uppercase tracking-widest text-[#94adcd] hover:text-[#4fdbc8] flex items-center gap-2"
        >
          ← <span>Tokoboros Index</span>
        </Link>
        <div className="text-[10px] uppercase tracking-widest text-[#4fdbc8] font-semibold">
          {entry.title} · screen {String(entry.id).padStart(2, "0")}
        </div>
        <div className="w-24 text-right text-[10px] text-[#94adcd]">live preview</div>
      </div>
      {body}
    </div>
  );
}
