import { useQuery } from "@tanstack/react-query";

import { getGatewayPermissionAudit } from "@/lib/api/tokoboros.functions";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const AUDIT_QUERY_KEY = ["gateway", "permission-audit"] as const;

type AuditEntry = {
  tool?: string;
  action?: string;
  decision?: string;
  rationale?: string;
  timestamp?: string;
  [key: string]: unknown;
};

function decisionVariant(decision: string): "default" | "secondary" | "destructive" | "outline" {
  if (decision === "approve") return "default";
  if (decision === "allow_once") return "secondary";
  if (decision === "deny") return "destructive";
  return "outline";
}

function decisionLabel(decision: string): string {
  if (decision === "approve") return "Allowed";
  if (decision === "allow_once") return "Allowed once";
  if (decision === "deny") return "Denied";
  return decision;
}

function plainLanguageTool(tool: string): string {
  const labels: Record<string, string> = {
    write_file: "write a file",
    read_file: "read a file",
    run_terminal_command: "run a terminal command",
    send_email: "send an email",
    delete_file: "delete a file",
    browse_web: "browse the web",
    create_calendar_event: "create a calendar event",
  };
  return labels[tool] ?? tool.replace(/_/g, " ");
}

function AuditCard({ entry }: { entry: AuditEntry }) {
  const tool = String(entry.tool ?? entry.action ?? "unknown action");
  const decision = String(entry.decision ?? "unknown");
  const rationale = String(entry.rationale ?? "");
  const ts = entry.timestamp ? new Date(entry.timestamp as string).toLocaleString() : null;

  return (
    <Card>
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between gap-2 flex-wrap">
          <CardTitle className="text-sm font-medium">
            Agent wanted to{" "}
            <span className="font-semibold text-foreground">{plainLanguageTool(tool)}</span>
          </CardTitle>
          <Badge variant={decisionVariant(decision)}>{decisionLabel(decision)}</Badge>
        </div>
      </CardHeader>
      <CardContent className="pt-0 flex flex-col gap-1">
        {rationale && (
          <p className="text-xs text-muted-foreground italic">"{rationale}"</p>
        )}
        {ts && <p className="text-xs text-muted-foreground mt-1">{ts}</p>}
      </CardContent>
    </Card>
  );
}

export function ApprovalsInboxScreen() {
  const auditQuery = useQuery({
    queryKey: AUDIT_QUERY_KEY,
    queryFn: () => getGatewayPermissionAudit({ data: { limit: 50 } }),
    staleTime: 15_000,
    refetchInterval: 30_000,
  });

  const entries: AuditEntry[] = Array.isArray(auditQuery.data) ? auditQuery.data : [];

  const pending = entries.filter(
    (e) => !e.decision || e.decision === "pending",
  );
  const recent = entries.filter(
    (e) => e.decision && e.decision !== "pending",
  );

  if (auditQuery.isLoading) {
    return (
      <div className="p-6 flex flex-col gap-3">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-20 rounded-xl" />
        ))}
      </div>
    );
  }

  if (auditQuery.isError) {
    return (
      <div className="p-6 text-destructive text-sm">
        Failed to load approvals. Make sure the gateway is running with an active session.
      </div>
    );
  }

  return (
    <div className="p-6 flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold">Approvals</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Actions your agent has requested or taken on your behalf.
        </p>
      </div>

      {pending.length > 0 && (
        <section className="flex flex-col gap-3">
          <h2 className="text-sm font-semibold text-amber-600 dark:text-amber-400">
            Waiting for your approval ({pending.length})
          </h2>
          {pending.map((entry, i) => (
            <AuditCard key={i} entry={entry} />
          ))}
        </section>
      )}

      <section className="flex flex-col gap-3">
        <h2 className="text-sm font-semibold text-muted-foreground">
          {recent.length === 0 ? "No activity yet" : `Recent activity (${recent.length})`}
        </h2>
        {recent.length === 0 ? (
          <p className="text-xs text-muted-foreground">
            Your agent has not requested any sensitive actions in this session.
          </p>
        ) : (
          recent.map((entry, i) => <AuditCard key={i} entry={entry} />)
        )}
      </section>
    </div>
  );
}
