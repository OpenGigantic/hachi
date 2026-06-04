export type JsonPrimitive = string | number | boolean | null | undefined;
export type JsonValue = JsonPrimitive | JsonObject | JsonValue[];
export interface JsonObject {
  [key: string]: JsonValue;
}

export interface RequestOptions {
  signal?: AbortSignal;
  headers?: HeadersInit;
  timeoutMs?: number;
}

export interface GatewayClientConfig {
  baseUrl: string;
  getAuthHeader?: () => string | Promise<string | undefined>;
}

export interface ApiErrorShape {
  status: number;
  code: string;
  message: string;
  details?: unknown;
}

export interface HealthResponse {
  status: string;
  service: string;
  protocol_version: string;
  time: string;
}

export interface ReadinessResponse {
  status: string;
  ready: boolean;
  blockers: string[];
  active_connections: number;
  time: string;
}

export interface SessionCreateRequest {
  client_id: string;
  surface: string;
  protocol_version: string;
  capabilities: string[];
}

export interface SessionCreateResponse {
  accepted: boolean;
  session_id: string;
  protocol_version: string;
  server_capabilities: string[];
}

export interface SessionMessageResponse {
  session_id: string;
  response: string;
}

export interface RuntimeEventDto {
  sequence?: number;
  event_type?: string;
  name?: string;
  payload: JsonObject;
  time?: string;
  timestamp?: string;
}

export interface SessionEventsResponse {
  session_id: string;
  events: RuntimeEventDto[];
  next_sequence: number;
}

export interface EventsQuery {
  sessionId: string;
  after?: number;
  limit?: number;
}

export interface SessionStateDto {
  session_id: string;
  active_skill?: string | null;
  last_user_query?: string;
  model_name?: string;
  boot_ready?: boolean;
  pending_job_count?: number;
  [key: string]: JsonValue;
}

export interface RecentSessionDto {
  id?: string;
  session_id?: string;
  started_at?: string;
  ended_at?: string;
  model?: string;
  total_turns?: number;
  [key: string]: JsonValue | undefined;
}

export interface McpInventoryDto {
  [key: string]: JsonValue;
}

export interface JobDto {
  [key: string]: JsonValue;
}

export interface JobFailureDto {
  [key: string]: JsonValue;
}

export interface PermissionAuditDto {
  [key: string]: JsonValue;
}

export interface SubagentReportDto {
  [key: string]: JsonValue;
}

export interface CapabilityStatusDto {
  id: string;
  kind: string;
  enabled: boolean;
  configured: boolean;
  secret_ref?: string | null;
}

export interface ProviderSettingsDto {
  kind: "anthropic" | "openai-compatible" | "ollama";
  model: string;
  base_url?: string;
  api_key_ref?: string;
  api_key_env?: string;
}

export interface PermissionsSettingsDto {
  tier: "sandbox" | "guided" | "unrestricted";
}

export interface MemorySettingsDto {
  enable_session_history: boolean;
  enable_semantic_memory: boolean;
}

export interface ResolvedConfigDto {
  provider: ProviderSettingsDto;
  permissions: PermissionsSettingsDto;
  memory: MemorySettingsDto;
  capabilities: Record<
    string,
    { enabled: boolean; configured: boolean; secret_ref?: string | null }
  >;
}

export interface UpdateProviderRequest {
  provider: Partial<ProviderSettingsDto>;
}

export interface ValidateProviderRequest {
  provider: Partial<ProviderSettingsDto>;
}

export interface ValidateProviderResponse {
  ok: boolean;
  errors: string[];
  warnings: string[];
}

export interface UpdatePermissionsRequest {
  permissions: Partial<PermissionsSettingsDto>;
}

export interface UpdateMemoryRequest {
  memory: Partial<MemorySettingsDto>;
}

export interface ModelOptionDto {
  id: string;
  label: string;
  provider: string;
  recommended: boolean;
}

export interface SetupStateDto {
  state: {
    setup_version: number;
    selected_tier?: string | null;
    completed_steps: string[];
    last_seen_release?: string | null;
    migration_markers: Record<string, string>;
    [key: string]: JsonValue;
  };
  needs_setup: boolean;
  environment: JsonObject;
}

export interface ApplySetupRequest {
  plan: {
    tier: "lightning" | "recommended" | "enhanced" | "power";
    steps: string[];
    answers: JsonObject;
    enable_memory: boolean;
    show_integrations: boolean;
    show_advanced: boolean;
  };
}

export interface ApplySetupResponse {
  ok: boolean;
  plan: JsonObject;
  state: JsonObject;
}

export interface TokoborosApiClient {
  health(options?: RequestOptions): Promise<HealthResponse>;
  readiness(options?: RequestOptions): Promise<ReadinessResponse>;
  connect(input: SessionCreateRequest, options?: RequestOptions): Promise<SessionCreateResponse>;
  chat(
    input: { sessionId: string; prompt: string },
    options?: RequestOptions,
  ): Promise<SessionMessageResponse>;
  events(input: EventsQuery, options?: RequestOptions): Promise<SessionEventsResponse>;
  sessionState(sessionId: string, options?: RequestOptions): Promise<SessionStateDto>;
  recentSessions(limit?: number, options?: RequestOptions): Promise<RecentSessionDto[]>;
  runtimeEvents(
    sessionId?: string,
    limit?: number,
    options?: RequestOptions,
  ): Promise<RuntimeEventDto[]>;
  mcpInventory(options?: RequestOptions): Promise<McpInventoryDto>;
  activeJobs(options?: RequestOptions): Promise<JobDto[]>;
  failedJobs(limit?: number, options?: RequestOptions): Promise<JobFailureDto[]>;
  permissionAudit(limit?: number, options?: RequestOptions): Promise<PermissionAuditDto[]>;
  subagentReports(
    query?: string,
    topK?: number,
    options?: RequestOptions,
  ): Promise<SubagentReportDto[]>;
  capabilities(options?: RequestOptions): Promise<CapabilityStatusDto[]>;
  getConfig(options?: RequestOptions): Promise<ResolvedConfigDto>;
  updateProvider(
    input: UpdateProviderRequest,
    options?: RequestOptions,
  ): Promise<ResolvedConfigDto>;
  validateProvider(
    input: ValidateProviderRequest,
    options?: RequestOptions,
  ): Promise<ValidateProviderResponse>;
  updatePermissions(
    input: UpdatePermissionsRequest,
    options?: RequestOptions,
  ): Promise<ResolvedConfigDto>;
  updateMemory(input: UpdateMemoryRequest, options?: RequestOptions): Promise<ResolvedConfigDto>;
  listModels(options?: RequestOptions): Promise<ModelOptionDto[]>;
  setupState(options?: RequestOptions): Promise<SetupStateDto>;
  applySetup(input: ApplySetupRequest, options?: RequestOptions): Promise<ApplySetupResponse>;
}
