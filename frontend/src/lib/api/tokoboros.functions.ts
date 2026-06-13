import { createServerFn } from "@tanstack/react-start";
import { z } from "zod";

import { getServerConfig } from "@/lib/config.server";
import { createTokoborosApiClient } from "@/lib/api/tokoboros.server";

const pollInput = z.object({
  sessionId: z.string().min(1),
  after: z.number().int().min(0).optional(),
  limit: z.number().int().min(1).max(500).optional(),
});

const connectInput = z.object({
  clientId: z.string().min(1),
  surface: z.string().min(1),
  capabilities: z.array(z.string()).default([]),
});

const promptInput = z.object({
  sessionId: z.string().min(1),
  prompt: z.string().min(1),
});

const sessionInput = z.object({
  sessionId: z.string().min(1),
});

const limitInput = z.object({
  limit: z.number().int().min(1).max(500).optional(),
});

const runtimeEventsInput = z.object({
  sessionId: z.string().optional(),
  limit: z.number().int().min(1).max(500).optional(),
});

const updateMemoryInput = z.object({
  enableSessionHistory: z.boolean().optional(),
  enableSemanticMemory: z.boolean().optional(),
});

const updatePermissionsInput = z.object({
  tier: z.enum(["sandbox", "guided", "unrestricted"]),
});

const updateProviderInput = z.object({
  providerKind: z.enum(["anthropic", "openai-compatible", "openrouter", "ollama"]),
  model: z.string().min(1),
  baseUrl: z.string().url().optional().or(z.literal("")),
  apiKeyEnv: z.string().min(1).optional().or(z.literal("")),
});

const validateProviderInput = updateProviderInput;

const applySetupInput = z.object({
  tier: z.enum(["lightning", "recommended", "enhanced", "power"]),
  providerKind: z.enum(["anthropic", "openai-compatible", "openrouter", "ollama"]),
  model: z.string().min(1),
});

const PROVIDER_KEY_ENV: Record<string, string> = {
  anthropic: "ANTHROPIC_API_KEY",
  "openai-compatible": "OPENAI_API_KEY",
  openrouter: "OPENROUTER_API_KEY",
  ollama: "",
};

function getClient() {
  const config = getServerConfig();
  return createTokoborosApiClient({
    baseUrl: config.gatewayBaseUrl,
    getAuthHeader: config.gatewayAuthHeader
      ? async () => config.gatewayAuthHeader as string
      : undefined,
  });
}

export const getGatewayReadiness = createServerFn({ method: "GET" }).handler(async () => {
  return getClient().readiness();
});

export const connectGatewaySession = createServerFn({ method: "POST" })
  .inputValidator(connectInput)
  .handler(async ({ data }) => {
    return getClient().connect({
      client_id: data.clientId,
      surface: data.surface,
      protocol_version: "1.0",
      capabilities: data.capabilities,
    });
  });

export const sendGatewayPrompt = createServerFn({ method: "POST" })
  .inputValidator(promptInput)
  .handler(async ({ data }) => {
    return getClient().chat({
      sessionId: data.sessionId,
      prompt: data.prompt,
    });
  });

export const getGatewayEvents = createServerFn({ method: "POST" })
  .inputValidator(pollInput)
  .handler(async ({ data }) => {
    return getClient().events({
      sessionId: data.sessionId,
      after: data.after,
      limit: data.limit,
    });
  });

export const getGatewaySessionState = createServerFn({ method: "POST" })
  .inputValidator(sessionInput)
  .handler(async ({ data }) => {
    return getClient().sessionState(data.sessionId);
  });

export const getGatewayRecentSessions = createServerFn({ method: "POST" })
  .inputValidator(limitInput)
  .handler(async ({ data }) => {
    return getClient().recentSessions(data.limit);
  });

export const getGatewayRuntimeEvents = createServerFn({ method: "POST" })
  .inputValidator(runtimeEventsInput)
  .handler(async ({ data }) => {
    return getClient().runtimeEvents(data.sessionId, data.limit ?? 100);
  });

export const getGatewayPermissionAudit = createServerFn({ method: "POST" })
  .inputValidator(limitInput)
  .handler(async ({ data }) => {
    return getClient().permissionAudit(data.limit ?? 50);
  });

const subagentReportsInput = z.object({
  query: z.string().optional(),
  topK: z.number().int().min(1).max(100).optional(),
});

export const getGatewaySubagentReports = createServerFn({ method: "POST" })
  .inputValidator(subagentReportsInput)
  .handler(async ({ data }) => {
    return getClient().subagentReports(data.query ?? "recent", data.topK ?? 20);
  });

export const getGatewayConfig = createServerFn({ method: "GET" }).handler(async () => {
  return getClient().getConfig();
});

export const updateGatewayMemory = createServerFn({ method: "POST" })
  .inputValidator(updateMemoryInput)
  .handler(async ({ data }) => {
    return getClient().updateMemory({
      memory: {
        enable_session_history: data.enableSessionHistory,
        enable_semantic_memory: data.enableSemanticMemory,
      },
    });
  });

export const updateGatewayPermissions = createServerFn({ method: "POST" })
  .inputValidator(updatePermissionsInput)
  .handler(async ({ data }) => {
    return getClient().updatePermissions({
      permissions: { tier: data.tier },
    });
  });

export const updateGatewayProvider = createServerFn({ method: "POST" })
  .inputValidator(updateProviderInput)
  .handler(async ({ data }) => {
    return getClient().updateProvider({
      provider: {
        kind: data.providerKind,
        model: data.model,
        base_url: data.baseUrl || undefined,
        api_key_env: data.apiKeyEnv || undefined,
      },
    });
  });

export const validateGatewayProvider = createServerFn({ method: "POST" })
  .inputValidator(validateProviderInput)
  .handler(async ({ data }) => {
    return getClient().validateProvider({
      provider: {
        kind: data.providerKind,
        model: data.model,
        base_url: data.baseUrl || undefined,
        api_key_env: data.apiKeyEnv || undefined,
      },
    });
  });

export const getGatewaySetupState = createServerFn({ method: "GET" }).handler(async () => {
  return getClient().setupState();
});

export const getGatewayModels = createServerFn({ method: "GET" }).handler(async () => {
  return getClient().listModels();
});

export const getGatewaySchedules = createServerFn({ method: "GET" }).handler(async () => {
  return getClient().listSchedules();
});

const scheduleIdInput = z.object({ scheduleId: z.string().min(1) });
const toggleScheduleInput = z.object({
  scheduleId: z.string().min(1),
  enabled: z.boolean(),
});

export const cancelGatewaySchedule = createServerFn({ method: "POST" })
  .inputValidator(scheduleIdInput)
  .handler(async ({ data }) => {
    return getClient().cancelSchedule(data.scheduleId);
  });

export const toggleGatewaySchedule = createServerFn({ method: "POST" })
  .inputValidator(toggleScheduleInput)
  .handler(async ({ data }) => {
    return getClient().toggleSchedule(data.scheduleId, data.enabled);
  });

export const applyGatewaySetup = createServerFn({ method: "POST" })
  .inputValidator(applySetupInput)
  .handler(async ({ data }) => {
    return getClient().applySetup({
      plan: {
        tier: data.tier,
        steps: ["welcome", "provider", "safety", "memory", "finish"],
        answers: {
          provider: {
            kind: data.providerKind,
            model: data.model,
            api_key_env: PROVIDER_KEY_ENV[data.providerKind] ?? "OPENAI_API_KEY",
          },
          permission_tier: "guided",
          memory: {
            enable_session_history: true,
            enable_semantic_memory: true,
          },
        },
        enable_memory: true,
        show_integrations: data.tier === "enhanced" || data.tier === "power",
        show_advanced: data.tier === "power",
      },
    });
  });
