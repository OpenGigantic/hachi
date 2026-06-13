import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import {
  cancelGatewaySchedule,
  getGatewaySchedules,
  toggleGatewaySchedule,
} from "@/lib/api/tokoboros.functions";
import type { ScheduleDto } from "@/types/api";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";

const SCHEDULES_QUERY_KEY = ["gateway", "schedules"] as const;

function recurrenceLabel(recurrence: string): string {
  if (recurrence.startsWith("every:")) {
    const secs = parseInt(recurrence.slice(6), 10);
    if (!isNaN(secs)) {
      if (secs < 60) return `every ${secs}s`;
      if (secs < 3600) return `every ${Math.round(secs / 60)}m`;
      return `every ${Math.round(secs / 3600)}h`;
    }
  }
  if (recurrence.startsWith("cron:")) return `cron: ${recurrence.slice(5)}`;
  if (recurrence.startsWith("once:")) return `once at ${recurrence.slice(5)}`;
  return recurrence;
}

function ScheduleCard({
  schedule,
  onToggle,
  onCancel,
}: {
  schedule: ScheduleDto;
  onToggle: (id: string, enabled: boolean) => void;
  onCancel: (id: string) => void;
}) {
  return (
    <Card className="flex flex-col gap-0">
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <CardTitle className="text-base leading-snug">{schedule.name}</CardTitle>
          <Badge variant={schedule.enabled ? "default" : "secondary"}>
            {schedule.enabled ? "active" : "paused"}
          </Badge>
        </div>
        <CardDescription className="text-xs font-mono">
          {recurrenceLabel(schedule.recurrence)}
        </CardDescription>
      </CardHeader>
      <CardContent className="flex flex-col gap-3 pt-0">
        <p className="text-sm text-muted-foreground line-clamp-2">{schedule.task}</p>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {schedule.last_fired_at && (
            <span>last ran {new Date(schedule.last_fired_at).toLocaleString()}</span>
          )}
          {schedule.fire_count !== undefined && (
            <span className="ml-auto">{schedule.fire_count}× fired</span>
          )}
        </div>
        <div className="flex gap-2">
          <Button
            size="sm"
            variant="outline"
            className="flex-1"
            onClick={() => onToggle(schedule.schedule_id, !schedule.enabled)}
          >
            {schedule.enabled ? "Pause" : "Resume"}
          </Button>
          <Button
            size="sm"
            variant="destructive"
            className="flex-1"
            onClick={() => onCancel(schedule.schedule_id)}
          >
            Delete
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

export function WorkflowsGalleryScreen() {
  const queryClient = useQueryClient();
  const [busy, setBusy] = useState<string | null>(null);

  const schedulesQuery = useQuery({
    queryKey: SCHEDULES_QUERY_KEY,
    queryFn: () => getGatewaySchedules(),
    staleTime: 10_000,
    refetchInterval: 30_000,
  });

  const schedules: ScheduleDto[] = schedulesQuery.data?.schedules ?? [];

  async function handleToggle(scheduleId: string, enabled: boolean) {
    setBusy(scheduleId);
    try {
      await toggleGatewaySchedule({ data: { scheduleId, enabled } });
      await queryClient.invalidateQueries({ queryKey: SCHEDULES_QUERY_KEY });
      toast.success(enabled ? "Schedule resumed." : "Schedule paused.");
    } catch {
      toast.error("Could not update schedule.");
    } finally {
      setBusy(null);
    }
  }

  async function handleCancel(scheduleId: string) {
    setBusy(scheduleId);
    try {
      await cancelGatewaySchedule({ data: { scheduleId } });
      await queryClient.invalidateQueries({ queryKey: SCHEDULES_QUERY_KEY });
      toast.success("Schedule deleted.");
    } catch {
      toast.error("Could not delete schedule.");
    } finally {
      setBusy(null);
    }
  }

  if (schedulesQuery.isLoading) {
    return (
      <div className="p-6 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-48 rounded-xl" />
        ))}
      </div>
    );
  }

  if (schedulesQuery.isError) {
    return (
      <div className="p-6 text-destructive text-sm">
        Failed to load schedules. Make sure the gateway is running with an active session.
      </div>
    );
  }

  return (
    <div className="p-6 flex flex-col gap-6">
      <div>
        <h1 className="text-2xl font-semibold">Workflows</h1>
        <p className="text-sm text-muted-foreground mt-1">
          Recurring tasks your agent runs automatically.{" "}
          {schedules.length > 0 && `${schedules.filter((s) => s.enabled).length} active.`}
        </p>
      </div>

      {schedules.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-20 gap-3 text-center">
          <p className="text-muted-foreground text-sm">No scheduled workflows yet.</p>
          <p className="text-xs text-muted-foreground max-w-xs">
            Ask your agent to set up a recurring task, e.g.{" "}
            <em>"summarise my inbox every morning at 8am"</em>.
          </p>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {schedules.map((s) => (
            <div key={s.schedule_id} className={busy === s.schedule_id ? "opacity-50" : ""}>
              <ScheduleCard schedule={s} onToggle={handleToggle} onCancel={handleCancel} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
