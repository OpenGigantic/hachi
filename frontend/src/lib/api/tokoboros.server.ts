import {
  type ApiErrorShape,
  type ApplySetupRequest,
  type ApplySetupResponse,
  type GatewayClientConfig,
  type HealthResponse,
  type ReadinessResponse,
  type RuntimeEventDto,
  type ScheduleDto,
  type ScheduleToggleResult,
  type SessionEventsResponse,
  type SessionStateDto,
  type RequestOptions,
  type ResolvedConfigDto,
  type SetupStateDto,
  type TokoborosApiClient,
  type UpdateMemoryRequest,
  type UpdatePermissionsRequest,
  type UpdateProviderRequest,
  type ValidateProviderRequest,
  type ValidateProviderResponse,
} from "@/types/api";
import createClient from "openapi-fetch";

import type { paths } from "@/generated/tokoboros-api";

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details?: unknown;

  constructor(input: ApiErrorShape) {
    super(input.message);
    this.name = "ApiError";
    this.status = input.status;
    this.code = input.code;
    this.details = input.details;
  }
}

function withTimeout(signal: AbortSignal | undefined, timeoutMs = 15000): AbortSignal {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);

  signal?.addEventListener(
    "abort",
    () => {
      clearTimeout(timer);
      ctrl.abort();
    },
    { once: true },
  );

  ctrl.signal.addEventListener(
    "abort",
    () => {
      clearTimeout(timer);
    },
    { once: true },
  );

  return ctrl.signal;
}

async function maybeJson(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    return undefined;
  }

  try {
    return await response.json();
  } catch {
    return undefined;
  }
}

function normalizeApiError(status: number, body: unknown, fallbackMessage: string): ApiError {
  if (body && typeof body === "object") {
    const payload = body as Record<string, unknown>;
    return new ApiError({
      status,
      code: String(payload.error ?? payload.code ?? "gateway_error"),
      message: String(payload.message ?? fallbackMessage),
      details: payload,
    });
  }

  return new ApiError({
    status,
    code: "gateway_error",
    message: fallbackMessage,
    details: body,
  });
}

export function createTokoborosApiClient(config: GatewayClientConfig): TokoborosApiClient {
  const baseUrl = config.baseUrl.replace(/\/$/, "");
  const client = createClient<paths>({ baseUrl });

  async function request<T>(
    execute: (
      signal: AbortSignal,
      headers: Headers,
    ) => Promise<{
      data?: unknown;
      error?: unknown;
      response: Response;
    }>,
    options?: RequestOptions,
  ): Promise<T> {
    const authHeader = config.getAuthHeader ? await config.getAuthHeader() : undefined;
    const headers = new Headers(options?.headers ?? {});

    if (authHeader) {
      headers.set("Authorization", authHeader);
    }

    let result: { data?: unknown; error?: unknown; response: Response };
    try {
      result = await execute(withTimeout(options?.signal, options?.timeoutMs), headers);
    } catch (error) {
      throw new ApiError({
        status: 0,
        code: "network_error",
        message: `Gateway request failed for ${baseUrl}. Check TOKOBOROS_GATEWAY_BASE_URL and gateway status.`,
        details: error,
      });
    }

    const body = result.error ?? (await maybeJson(result.response));
    if (!result.response.ok) {
      throw normalizeApiError(
        result.response.status,
        body,
        `Gateway request failed (${result.response.status})`,
      );
    }

    if (result.data === undefined) {
      throw new ApiError({
        status: result.response.status,
        code: "protocol_error",
        message: "Gateway returned a non-JSON response.",
      });
    }

    return result.data as T;
  }

  return {
    health(options) {
      return request<HealthResponse>(
        (signal, headers) => client.GET("/health", { signal, headers }),
        options,
      );
    },
    readiness(options) {
      return request<ReadinessResponse>(
        (signal, headers) => client.GET("/readiness", { signal, headers }),
        options,
      );
    },
    connect(input, options) {
      return request(
        (signal, headers) => client.POST("/sessions", { body: input, signal, headers }),
        options,
      );
    },
    chat(input, options) {
      return request(
        (signal, headers) =>
          client.POST("/sessions/{session_id}/messages", {
            params: { path: { session_id: input.sessionId } },
            body: { prompt: input.prompt },
            signal,
            headers,
          }),
        options,
      );
    },
    events(input, options) {
      return request<SessionEventsResponse>(
        (signal, headers) =>
          client.GET("/sessions/{session_id}/events", {
            params: {
              path: { session_id: input.sessionId },
              query: { after: input.after ?? 0, limit: input.limit ?? 100 },
            },
            signal,
            headers,
          }),
        options,
      );
    },
    sessionState(sessionId, options) {
      return request<SessionStateDto>(
        (signal, headers) =>
          client.GET("/sessions/{session_id}", {
            params: { path: { session_id: sessionId } },
            signal,
            headers,
          }),
        options,
      );
    },
    recentSessions(limit, options) {
      return request(
        (signal, headers) =>
          client.GET("/sessions", {
            params: { query: { limit: limit ?? 10 } },
            signal,
            headers,
          }),
        options,
      );
    },
    runtimeEvents(sessionId, limit, options) {
      return request<RuntimeEventDto[]>(
        (signal, headers) =>
          client
            .GET("/runtime/events", {
              params: { query: { session_id: sessionId || undefined, limit: limit ?? 100 } },
              signal,
              headers,
            })
            .then((response) => ({
              ...response,
              data: (response.data ?? []) as unknown as RuntimeEventDto[],
            })),
        options,
      );
    },
    mcpInventory(options) {
      return request(
        (signal, headers) => client.GET("/runtime/mcp/inventory", { signal, headers }),
        options,
      );
    },
    activeJobs(options) {
      return request((signal, headers) => client.GET("/jobs/active", { signal, headers }), options);
    },
    failedJobs(limit, options) {
      return request(
        (signal, headers) =>
          client.GET("/jobs/failed", {
            params: { query: { limit: limit ?? 20 } },
            signal,
            headers,
          }),
        options,
      );
    },
    permissionAudit(limit, options) {
      return request(
        (signal, headers) =>
          client.GET("/permissions/audit", {
            params: { query: { limit: limit ?? 50 } },
            signal,
            headers,
          }),
        options,
      );
    },
    subagentReports(query, topK, options) {
      return request(
        (signal, headers) =>
          client.GET("/subagents/reports", {
            params: { query: { query: query ?? "recent delegation", top_k: topK ?? 10 } },
            signal,
            headers,
          }),
        options,
      );
    },
    capabilities(options) {
      return request(
        (signal, headers) => client.GET("/capabilities", { signal, headers }),
        options,
      );
    },
    getConfig(options) {
      return request<ResolvedConfigDto>(
        (signal, headers) => client.GET("/config", { signal, headers }),
        options,
      );
    },
    updateProvider(input, options) {
      return request<ResolvedConfigDto>(
        (signal, headers) =>
          client.PUT("/config/provider", {
            body: input,
            signal,
            headers,
          }),
        options,
      );
    },
    validateProvider(input, options) {
      return request<ValidateProviderResponse>(
        (signal, headers) =>
          client.POST("/config/provider/validate", {
            body: input,
            signal,
            headers,
          }),
        options,
      );
    },
    updatePermissions(input, options) {
      return request<ResolvedConfigDto>(
        (signal, headers) =>
          client.PUT("/config/permissions", {
            body: input,
            signal,
            headers,
          }),
        options,
      );
    },
    updateMemory(input, options) {
      return request<ResolvedConfigDto>(
        (signal, headers) =>
          client.PUT("/config/memory", {
            body: input,
            signal,
            headers,
          }),
        options,
      );
    },
    listModels(options) {
      return request((signal, headers) => client.GET("/models", { signal, headers }), options);
    },
    setupState(options) {
      return request<SetupStateDto>(
        (signal, headers) => client.GET("/setup", { signal, headers }),
        options,
      );
    },
    applySetup(input, options) {
      return request<ApplySetupResponse>(
        (signal, headers) =>
          client.POST("/setup/apply", {
            body: input,
            signal,
            headers,
          }),
        options,
      );
    },
    listSchedules(options) {
      return request<{ schedules: ScheduleDto[] }>(
        (signal, headers) => client.GET("/schedules", { signal, headers }),
        options,
      );
    },
    cancelSchedule(scheduleId, options) {
      return request<ScheduleToggleResult>(
        (signal, headers) =>
          client.DELETE("/schedules/{schedule_id}", {
            params: { path: { schedule_id: scheduleId } },
            signal,
            headers,
          }),
        options,
      );
    },
    toggleSchedule(scheduleId, enabled, options) {
      return request<ScheduleToggleResult>(
        (signal, headers) =>
          client.PUT("/schedules/{schedule_id}/enabled", {
            params: { path: { schedule_id: scheduleId } },
            body: { enabled },
            signal,
            headers,
          }),
        options,
      );
    },
  };
}

export type {
  ApplySetupRequest,
  UpdateMemoryRequest,
  UpdatePermissionsRequest,
  UpdateProviderRequest,
  ValidateProviderRequest,
};
