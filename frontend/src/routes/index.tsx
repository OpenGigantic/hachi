import { createFileRoute, Link } from "@tanstack/react-router";

import { useGatewayReadinessQuery } from "@/hooks/use-gateway-queries";

const screens = [
  { id: 2, slug: "main", title: "Main Console", desc: "Primary operator thread and tool calls" },
  { id: 15, slug: "operator", title: "Operator Console", desc: "Full operator workspace" },
  { id: 14, slug: "console", title: "Console", desc: "Tokoboros console view" },
  { id: 10, slug: "agents", title: "Agents Inventory", desc: "Registered agents and status" },
  { id: 12, slug: "logs", title: "System Logs and Telemetry", desc: "Realtime logs stream" },
  { id: 3, slug: "runtime", title: "Runtime Configuration", desc: "Tune runtime parameters" },
  { id: 4, slug: "settings", title: "Settings Panel", desc: "Global settings" },
  { id: 5, slug: "setup", title: "System Setup", desc: "Initial system setup" },
  { id: 8, slug: "setup-2", title: "System Setup II", desc: "Continued setup flow" },
  { id: 7, slug: "models", title: "Model Selection", desc: "Pick the active model" },
  { id: 9, slug: "deployment", title: "Deployment Success", desc: "Post-deploy confirmation" },
  { id: 6, slug: "tool-output", title: "Tool Output Block", desc: "Rendered tool result spec" },
  { id: 13, slug: "input-states", title: "Input Area States", desc: "Composer interaction states" },
  { id: 11, slug: "error", title: "Configuration Error", desc: "System configuration error state" },
];

export const Route = createFileRoute("/")({
  head: () => ({
    meta: [
      { title: "Tokoboros - Operator Workbench" },
      {
        name: "description",
        content:
          "Tokoboros AI operator workbench - sessions, agents, logs, runtime, and deployment.",
      },
      { property: "og:title", content: "Tokoboros - Operator Workbench" },
      {
        property: "og:description",
        content: "AI operator workbench with sessions, agents, logs and deployment surfaces.",
      },
    ],
  }),
  component: Index,
});

function Index() {
  const readiness = useGatewayReadinessQuery();
  const readinessPayload = readiness.data as { ready?: boolean } | undefined;
  const isReady = readinessPayload?.ready === true;
  const statusText = readiness.isPending
    ? "Gateway status checking"
    : readiness.isError
      ? "Gateway unreachable"
      : isReady
        ? "Gateway ready"
        : "Gateway not ready";
  const statusColor = readiness.isError
    ? "bg-[#f97316]"
    : isReady
      ? "bg-[#4fdbc8]"
      : "bg-[#eab308]";

  return (
    <div className="min-h-screen bg-[#070f18] font-mono text-[#d5e7ff]">
      <header className="flex items-center justify-between border-b border-[#304a65] px-8 py-6">
        <div className="flex items-center gap-4">
          <div className="flex h-10 w-10 items-center justify-center rounded-md border border-[#4fdbc8]/30 bg-[#4fdbc8]/10 font-black text-[#4fdbc8]">
            T
          </div>
          <div>
            <div className="text-xl font-black tracking-tighter text-[#4fdbc8]">Tokoboros</div>
            <div className="text-[10px] uppercase tracking-widest text-[#94adcd]">
              v1.2.0-stable · operator workbench
            </div>
          </div>
        </div>
        <div className="hidden items-center gap-2 rounded border border-[#304a65] bg-[#0a1b2b] px-3 py-1.5 md:flex">
          <span className={`h-1.5 w-1.5 rounded-full ${statusColor}`} />
          <span className="text-[10px] font-semibold uppercase tracking-widest text-[#d5e7ff]">
            {statusText}
          </span>
        </div>
      </header>

      <main className="mx-auto max-w-7xl px-8 py-12">
        <div className="mb-10">
          <div className="mb-2 text-[10px] uppercase tracking-widest text-[#94adcd]">
            Screens · {screens.length}
          </div>
          <h1 className="max-w-2xl text-3xl leading-tight tracking-tight text-[#d5e7ff] md:text-4xl">
            Every surface of the Tokoboros operator workbench, wired and ready to inspect.
          </h1>
        </div>

        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {screens.map((s) => (
            <Link
              key={s.slug}
              to="/s/$slug"
              params={{ slug: s.slug }}
              className="group relative block overflow-hidden rounded-md border border-[#304a65] bg-[#081421] p-5 transition-all hover:border-[#4fdbc8] hover:bg-[#0a1b2b]"
            >
              <div className="mb-3 flex items-start justify-between">
                <span className="text-[10px] font-semibold uppercase tracking-widest text-[#94adcd]">
                  Screen / {String(s.id).padStart(2, "0")}
                </span>
                <span className="text-xs text-[#4fdbc8] opacity-0 transition-opacity group-hover:opacity-100">
                  {">"}
                </span>
              </div>
              <div className="mb-1 text-lg font-black tracking-tight text-[#d5e7ff] transition-colors group-hover:text-[#4fdbc8]">
                {s.title}
              </div>
              <div className="text-xs leading-relaxed text-[#94adcd]">{s.desc}</div>
              <div className="absolute inset-x-0 bottom-0 h-px bg-gradient-to-r from-transparent via-[#4fdbc8]/40 to-transparent opacity-0 transition-opacity group-hover:opacity-100" />
            </Link>
          ))}
        </div>

        <footer className="mt-16 flex items-center justify-between border-t border-[#304a65] pt-6 text-[10px] uppercase tracking-widest text-[#94adcd]">
          <span>Tokoboros operator workbench</span>
          <span>Surface map preview</span>
        </footer>
      </main>
    </div>
  );
}
