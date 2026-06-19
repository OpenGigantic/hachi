// ============================================================
// Hachi Web App — Zustand store
// Single store; persist only operator + theme.
// Actions mirror prototype logic methods (dc.html 1800–2160).
// ============================================================

import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { USE_MOCKS } from "../data/client";
import {
  initApprovals,
  history,
  runs,
  routines,
  finished,
  failed,
  memGroups,
  blocked,
  proposed,
  skills,
  kennel,
  integrations,
  mcpServers,
  mcpTools,
  ogNodes,
  authTargets,
  authSource,
  spendTurns,
  spendTidies,
  spendModels,
  packRoster,
  events,
  toolCalls,
  auditLog,
  recall,
  sessions,
  skillUsage,
  askRules,
  allowedFolders,
  allowedCommands,
  opTools,
  initialSpent,
  initialBudget,
  initialTaskSpent,
} from "../data/fixtures";
import type {
  Approval,
  HistoryItem,
  Run,
  Routine,
  FinishedTask,
  FailedTask,
  MemGroup,
  BlockedItem,
  ProposedChange,
  Skill,
  KennelItem,
  Integration,
  McpServer,
  McpTool,
  OgNode,
  AuthTarget,
  DiffLine,
  SpendTurn,
  SpendTidy,
  SpendModel,
  PackMember,
  ActivityEvent,
  ToolCall,
  AuditEntry,
  RecallHit,
  Session,
  SkillUsage,
  AskRule,
  OpTool,
  Toast,
  ChatMessage,
  ActionCard,
  RunPackMember,
  InContextLeash,
  Provider,
  SafetyLevel,
} from "../data/types";

// Alias used in consent overlay state
type KennelConsentItem = KennelItem;

// ---- run-queue item type (internal) ----
interface RunQueueItem {
  line?: { text: string; tone?: string };
  steps?: { i: number; state: string }[];
  pack?: { id: string; status: string; task?: string };
  spend?: number;
  leash?: { request: string; action: string; why: string };
}

// ============================================================
// State shape
// ============================================================
interface State {
  // persisted
  operator: boolean;
  theme: "light" | "dark";
  // data source: "live" calls the gateway; "mock" keeps seeded fixtures.
  dataMode: "mock" | "live";
  // budget
  spent: number;
  budget: number;
  taskSpent: number;
  // domain arrays
  approvals: Approval[];
  history: HistoryItem[];
  runs: Run[];
  routines: Routine[];
  finished: FinishedTask[];
  failed: FailedTask[];
  memGroups: MemGroup[];
  blocked: BlockedItem[];
  proposed: ProposedChange | null;
  skills: Skill[];
  kennel: KennelItem[];
  integrations: Integration[];
  mcpServers: McpServer[];
  mcpTools: McpTool[];
  ogNodes: OgNode[];
  authTargets: AuthTarget[];
  authSource: DiffLine[];
  authSel: string;
  authMode: "diff" | "source";
  authApplied: boolean;
  ogSel: string;
  ogView: "graph" | "tree";
  spendTurns: SpendTurn[];
  spendTidies: SpendTidy[];
  spendModels: SpendModel[];
  packRoster: PackMember[];
  events: ActivityEvent[];
  toolCalls: ToolCall[];
  auditLog: AuditEntry[];
  recall: RecallHit[];
  sessions: Session[];
  skillUsage: SkillUsage[];
  safety: SafetyLevel;
  askRules: AskRule[];
  allowedFolders: string[];
  allowedCommands: string[];
  opTools: OpTool[];
  provider: Provider;
  reconnecting: boolean;
  // toast
  toast: Toast | null;
  // palette
  palette: { open: boolean; query: string };
  // setup
  setup: {
    open: boolean;
    step: number;
    key: string;
    safety: SafetyLevel;
    routine: boolean;
    google: boolean;
    channel: boolean;
  };
  // kennel consent overlay
  kennelConsent: KennelConsentItem | null;
  // chat
  slashOpen: boolean;
  streaming: boolean;
  inContext: InContextLeash | null;
  messages: ChatMessage[];
  composer: string;
  // home
  homeInput: string;
  selectedWork: unknown | null;
  workFilter: string;
  // live run
  runView: Run | null;
  runLines: { text: string; tone?: string }[];
  runStreaming: boolean;
  runDone: boolean;
  runLeash: { request: string; action: string; why: string } | null;
  runSpent: number;
  runPack: RunPackMember[];
  runSteps: { label: string; state: string }[];
  // memory
  editingFactId: string | null;
  editingText: string;
}

// ============================================================
// Actions shape
// ============================================================
interface Actions {
  // live-data hydration (foundation)
  // Shallow-merge a slice of gateway-derived state into the store. Domain
  // loaders call this after mapping a gateway response to UI types.
  hydrate: (patch: Partial<State>) => void;
  setReconnecting: (v: boolean) => void;
  // operator / theme
  setOperator: (v: boolean) => void;
  setTheme: (t: "light" | "dark") => void;
  toggleTheme: () => void;
  toggleOperator: () => void;
  // toast
  showToast: (message: string, tone?: Toast["tone"]) => void;
  dismissToast: () => void;
  // palette
  openPalette: () => void;
  closePalette: () => void;
  setPaletteQuery: (q: string) => void;
  // setup
  openSetup: () => void;
  closeSetup: () => void;
  setSetupStep: (n: number) => void;
  setSetupField: (k: string, v: unknown) => void;
  setupBack: () => void;
  setupNext: () => void;
  toggleSetupRoutine: () => void;
  toggleSetupGoogle: () => void;
  toggleSetupChannel: () => void;
  setSetupKey: (v: string) => void;
  // approvals
  allowApproval: (id: number, scope?: string) => void;
  alwaysApproval: (id: number) => void;
  declineApproval: (id: number, reason?: string) => void;
  declineAll: () => void;
  // auto-tick (called by interval)
  tickApprovals: () => void;
  // routines / work
  pauseRoutine: (id: number) => void;
  runRoutineNow: (id?: number) => void;
  cancelRun: (id: number) => void;
  retryFailed: (id: number) => void;
  duplicateRoutine: (id: number) => void;
  openWork: (item: unknown) => void;
  closeWork: () => void;
  setWorkFilter: (f: string) => void;
  // memory
  addFact: (groupId?: string, text?: string) => void;
  editFact: (id: string, text: string) => void;
  deleteFact: (id: string) => void;
  startEditFact: (id: string) => void;
  cancelEdit: () => void;
  saveFact: (groupId: string, factId: string) => void;
  delFact: (groupId: string, factId: string) => void;
  setEditText: (v: string) => void;
  approveProposed: () => void;
  rejectProposed: () => void;
  // skills / kennel
  addSkill: (kennelId: string) => void;
  confirmKennel: () => void;
  cancelKennel: () => void;
  removeSkill: (id: string) => void;
  askKennel: (item: KennelConsentItem) => void;
  // OG
  setOgSel: (id: string) => void;
  setOgView: (v: "graph" | "tree") => void;
  setOgHover: (id: string | null) => void;
  // authoring
  selectAuthTarget: (name: string) => void;
  setAuthMode: (m: "diff" | "source") => void;
  applyPatch: () => void;
  revertPatch: () => void;
  rejectPatch: () => void;
  // integrations
  connectIntegration: (id: string) => void;
  disconnectIntegration: (id: string) => void;
  // settings
  setSafety: (level: SafetyLevel) => void;
  toggleAskRule: (id: string) => void;
  removeFolder: (f: string) => void;
  removeCommand: (c: string) => void;
  addFolder: () => void;
  addCommand: () => void;
  toggleOpTool: (id: string) => void;
  setProvider: (p: Partial<Provider>) => void;
  // chat / streaming
  startTask: (text: string) => void;
  sendMessage: (text: string) => void;
  stopStreaming: () => void;
  setComposer: (v: string) => void;
  setHomeInput: (v: string) => void;
  icAllow: () => void;
  icDeny: () => void;
  toggleCard: (mid: number, key: string) => void;
  dryRunRoutine: (msgId: number) => void;
  createRoutine: (msgId: number) => void;
  // live run
  openRun: (runId?: number, from?: string) => void;
  watchLive: (runId: number) => void;
  closeRun: () => void;
  runAllowLeash: () => void;
  runDenyLeash: () => void;
  stopRun: () => void;
  // reconnect
  simulateReconnect: () => void;
}

// ============================================================
// Internal id counter (module-level, not in state)
// ============================================================
let _id = 100;
function nextId() { return ++_id; }

// ============================================================
// Streaming helpers (module-level timers)
// ============================================================
let _iv: ReturnType<typeof setInterval> | null = null;
let _tt: ReturnType<typeof setTimeout> | null = null;
let _rc: ReturnType<typeof setTimeout> | null = null;
let _runIv: ReturnType<typeof setInterval> | null = null;
let _runQueue: RunQueueItem[] = [];
let _runFrom = "home";
let _runStart = 0;

function clearIv() { if (_iv) { clearInterval(_iv); _iv = null; } }
function clearRunIv() { if (_runIv) { clearInterval(_runIv); _runIv = null; } }

// ============================================================
// Store
// ============================================================
const domainDefaults = (): Omit<State, "operator" | "theme"> => ({
  dataMode: USE_MOCKS ? "mock" : "live",
  spent: initialSpent,
  budget: initialBudget,
  taskSpent: initialTaskSpent,
  approvals: initApprovals(),
  history,
  runs,
  routines,
  finished,
  failed,
  memGroups,
  blocked,
  proposed,
  skills,
  kennel,
  integrations,
  mcpServers,
  mcpTools,
  ogNodes,
  authTargets,
  authSource,
  authSel: "deep_research.og",
  authMode: "diff",
  authApplied: false,
  ogSel: "brain",
  ogView: "graph",
  spendTurns,
  spendTidies,
  spendModels,
  packRoster,
  events,
  toolCalls,
  auditLog,
  recall,
  sessions,
  skillUsage,
  safety: "standard",
  askRules,
  allowedFolders,
  allowedCommands,
  opTools,
  provider: { name: "anthropic", model: "claude-sonnet-4-5", key: "" },
  reconnecting: false,
  toast: null,
  palette: { open: false, query: "" },
  setup: { open: false, step: 0, key: "", safety: "standard", routine: true, google: false, channel: false },
  kennelConsent: null,
  slashOpen: false,
  streaming: false,
  inContext: null,
  messages: [],
  composer: "",
  homeInput: "",
  selectedWork: null,
  workFilter: "active",
  runView: null,
  runLines: [],
  runStreaming: false,
  runDone: false,
  runLeash: null,
  runSpent: 0,
  runPack: [],
  runSteps: [],
  editingFactId: null,
  editingText: "",
});

export const useStore = create<State & Actions>()(
  persist(
    (set, get) => {
      // ---- helpers ----
      const toast = (message: string, tone?: Toast["tone"]) => {
        set({ toast: { message, tone } });
        if (_tt) clearTimeout(_tt);
        _tt = setTimeout(() => set({ toast: null }), 3200);
      };

      const getText = (id: number): string => {
        const m = get().messages.find((x) => x.id === id);
        return m ? m.text : "";
      };

      const setText = (id: number, t: string) => {
        set((s) => ({
          messages: s.messages.map((m) => (m.id === id ? { ...m, text: t } : m)),
        }));
      };

      const stream = (id: number, str: string, done?: () => void) => {
        const base = getText(id);
        let i = 0;
        clearIv();
        _iv = setInterval(() => {
          i += 2;
          setText(id, base + str.slice(0, i));
          if (i >= str.length) {
            clearIv();
            if (done) setTimeout(done, 380);
          }
        }, 16);
      };

      const addCard = (id: number, card: ActionCard) => {
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === id ? { ...m, cards: [...m.cards, card] } : m
          ),
        }));
      };

      const setCard = (id: number, key: string, patch: Partial<ActionCard>) => {
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === id
              ? { ...m, cards: m.cards.map((c) => (c.key === key ? { ...c, ...patch } : c)) }
              : m
          ),
        }));
      };

      const setRoutine = (id: number, patch: Record<string, unknown>) => {
        set((s) => ({
          messages: s.messages.map((m) =>
            m.id === id
              ? { ...m, routine: { ...(m.routine || {}), ...patch } }
              : m
          ),
        }));
      };

      const resolveApproval = (
        id: number,
        decision: string,
        color: string
      ) => {
        const a = get().approvals.find((x) => x.id === id);
        const historyEntry: HistoryItem | undefined = a
          ? {
              id,
              icon:
                color === "var(--hachi-danger)" ? "x" : "circle-check",
              request: a.request,
              decision,
              when: "just now",
              color,
            }
          : undefined;
        const auditEntry: AuditEntry = {
          ts: new Date().toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" }),
          op: a?.op ?? "unknown",
          target: a?.action ?? "",
          policy: a?.policy ?? "",
          decision:
            decision === "allowed once"
              ? "allowed once"
              : decision === "always allowed"
              ? "allowed"
              : "declined",
        };
        set((s) => ({
          approvals: s.approvals.filter((x) => x.id !== id),
          history: historyEntry
            ? [historyEntry, ...s.history]
            : s.history,
          auditLog: [auditEntry, ...s.auditLog],
        }));
      };

      // ---- run helpers ----
      const applyRunSteps = (arr: { i: number; state: string }[]) => {
        set((s) => ({
          runSteps: s.runSteps.map((st, i) => {
            const u = arr.find((a) => a.i === i);
            return u ? { ...st, state: u.state } : st;
          }),
        }));
      };

      const archiveRun = (): Partial<State> => {
        const s = get();
        const rv = s.runView;
        if (!rv) return {};
        const finItem: FinishedTask = {
          id: rv.id,
          title: rv.title,
          note: "Hachi is resting",
          when: "just now",
          cost: s.runSpent,
          result: "Summarised 6 PRs — 2 need review. Posted to #eng.",
          steps: s.runSteps.map((st) => ({
            label: st.label,
            state: (st.state === "working" ? "done" : st.state) as "done" | "working" | "pending" | "failed",
          })),
        };
        return {
          runs: s.runs.filter((r) => r.id !== rv.id),
          finished: [finItem, ...s.finished],
          spent:
            Math.round(
              (s.spent + Math.max(0, s.runSpent - (rv.spent || 0))) * 100
            ) / 100,
        };
      };

      const finishRun = () => {
        clearRunIv();
        const s = get();
        if (s.runDone || !s.runView) return;
        const elapsed = _runStart
          ? Math.ceil((Date.now() - _runStart) / 1000)
          : 0;
        const elStr = elapsed ? " · " + (elapsed < 60 ? elapsed + "s" : Math.floor(elapsed / 60) + "m " + (elapsed % 60) + "s") : "";
        set({
          ...archiveRun(),
          runStreaming: false,
          runDone: true,
          runLines: [
            ...s.runLines,
            { text: "done — Hachi is resting" + elStr, tone: "success" },
          ],
          runSteps: s.runSteps.map((st) => ({ ...st, state: "done" })),
          runPack: s.runPack.map((p) => ({ ...p, status: "done" as const })),
        });
        toast("Task complete — Hachi is resting", "success");
      };

      const runTick = () => {
        const s = get();
        if (s.runDone || !s.runView) { clearRunIv(); return; }
        if (!_runQueue || _runQueue.length === 0) { finishRun(); return; }
        const item = _runQueue.shift()!;
        if (item.steps) applyRunSteps(item.steps);
        if (item.leash) {
          clearRunIv();
          set({ runLeash: item.leash, runStreaming: false });
          return;
        }
        set((prev) => {
          let pack = prev.runPack;
          if (item.pack) {
            pack = pack.map((p) =>
              p.id === item.pack!.id ? { ...p, ...item.pack } : p
            ) as RunPackMember[];
          }
          return {
            runLines: item.line ? [...prev.runLines, item.line] : prev.runLines,
            runPack: pack,
            runSpent: item.spend
              ? Math.round((prev.runSpent + item.spend) * 100) / 100
              : prev.runSpent,
          };
        });
      };

      const slug = (t: string) =>
        String(t || "task")
          .toLowerCase()
          .replace(/[^a-z0-9]+/g, "-")
          .replace(/^-|-$/g, "")
          .slice(0, 28) || "task";

      // ---- actions ----
      return {
        // seed domain state
        ...domainDefaults(),
        // persisted defaults (will be overridden by storage)
        operator: false,
        theme: "light",

        // live-data hydration (foundation)
        hydrate: (patch) => set(patch as Partial<State & Actions>),
        setReconnecting: (v) => set({ reconnecting: v }),

        // operator / theme
        setOperator: (v) => set({ operator: v }),
        setTheme: (t) => {
          document.documentElement.setAttribute("data-theme", t);
          set({ theme: t });
        },
        toggleTheme: () => {
          const t = get().theme === "dark" ? "light" : "dark";
          document.documentElement.setAttribute("data-theme", t);
          set({ theme: t });
        },
        toggleOperator: () => set((s) => ({ operator: !s.operator })),

        // toast
        showToast: (message, tone) => toast(message, tone),
        dismissToast: () => set({ toast: null }),

        // palette
        openPalette: () => set({ palette: { open: true, query: "" } }),
        closePalette: () => set((s) => ({ palette: { ...s.palette, open: false } })),
        setPaletteQuery: (q) => set((s) => ({ palette: { ...s.palette, query: q } })),

        // setup
        openSetup: () =>
          set((s) => ({ setup: { ...s.setup, open: true, step: 0 } })),
        closeSetup: () =>
          set((s) => ({ setup: { ...s.setup, open: false } })),
        setSetupStep: (n) =>
          set((s) => ({ setup: { ...s.setup, step: n } })),
        setSetupField: (k, v) =>
          set((s) => ({ setup: { ...s.setup, [k]: v } })),
        setSetupKey: (v) =>
          set((s) => ({ setup: { ...s.setup, key: v } })),
        toggleSetupRoutine: () =>
          set((s) => ({ setup: { ...s.setup, routine: !s.setup.routine } })),
        toggleSetupGoogle: () =>
          set((s) => ({ setup: { ...s.setup, google: !s.setup.google } })),
        toggleSetupChannel: () =>
          set((s) => ({ setup: { ...s.setup, channel: !s.setup.channel } })),
        setupBack: () =>
          set((s) => ({
            setup: { ...s.setup, step: Math.max(0, s.setup.step - 1) },
          })),
        setupNext: () => {
          const step = get().setup.step;
          if (step >= 4) {
            const su = get().setup;
            set((s) => {
              const hasBriefing = s.routines.some((r) => r.title === "Morning briefing");
              const newRoutines =
                su.routine && !hasBriefing
                  ? [
                      {
                        id: nextId(),
                        title: "Morning briefing",
                        schedule: "weekdays 8:00",
                        next: "tomorrow 8:00",
                        cost: 0.04,
                        paused: false,
                      },
                      ...s.routines,
                    ]
                  : s.routines;
              return {
                setup: { ...s.setup, open: false },
                safety: su.safety,
                routines: newRoutines,
              };
            });
            toast("All set — Hachi is on watch", "success");
          } else {
            set((s) => ({ setup: { ...s.setup, step: s.setup.step + 1 } }));
          }
        },

        // approvals
        allowApproval: (id) => {
          resolveApproval(id, "allowed once", "var(--hachi-success)");
          toast("On it — Hachi is working", "working");
        },
        alwaysApproval: (id) => {
          resolveApproval(id, "always allowed", "var(--hachi-success)");
          toast("Saved — Hachi won't ask again for this", "success");
        },
        declineApproval: (id) => {
          resolveApproval(id, "declined", "var(--hachi-danger)");
          toast("Stayed put — Hachi is resting", "success");
        },
        declineAll: () => {
          const ids = get().approvals.map((a) => a.id);
          ids.forEach((id) =>
            resolveApproval(id, "declined", "var(--hachi-danger)")
          );
          toast("Stayed put — Hachi is resting", "success");
        },
        tickApprovals: () => {
          const now = Date.now();
          const expired = get().approvals.filter((a) => a.expiresAt <= now);
          if (expired.length) {
            set((s) => ({
              approvals: s.approvals.filter((a) => a.expiresAt > now),
              history: [
                ...expired.map((a) => ({
                  id: a.id,
                  icon: "clock",
                  request: a.request,
                  decision: "timed out — Hachi stayed put",
                  when: "just now",
                  color: "var(--hachi-muted)",
                })),
                ...s.history,
              ],
            }));
          }
        },

        // routines / work
        pauseRoutine: (id) =>
          set((s) => ({
            routines: s.routines.map((r) =>
              r.id === id ? { ...r, paused: !r.paused } : r
            ),
          })),
        runRoutineNow: () => {
          set({ selectedWork: null });
          toast("Running now — Hachi is working", "working");
        },
        cancelRun: (id) =>
          set((s) => ({ runs: s.runs.filter((r) => r.id !== id) })),
        retryFailed: (id) => {
          const f = get().failed.find((x) => x.id === id);
          set((s) => ({
            failed: s.failed.filter((x) => x.id !== id),
            runs: f
              ? [
                  {
                    id: nextId(),
                    title: f.title,
                    status: "working" as const,
                    progress: 0.12,
                    spent: 0.0,
                    budget: 1.0,
                    meta: "Retrying",
                    owner: f.owner,
                    when: "retrying now",
                    steps: (f.steps || []).map((stp) => ({
                      label: stp.label,
                      state: "done" as const,
                    })),
                  },
                  ...s.runs,
                ]
              : s.runs,
            selectedWork: null,
            workFilter: "active",
          }));
          toast("Retrying — Hachi is working", "working");
        },
        duplicateRoutine: (id) => {
          const r = get().routines.find((x) => x.id === id);
          if (!r) return;
          set((s) => ({
            routines: [
              { ...r, id: nextId(), title: r.title + " (copy)" },
              ...s.routines,
            ],
          }));
        },
        openWork: (item) => set({ selectedWork: item }),
        closeWork: () => set({ selectedWork: null }),
        setWorkFilter: (f) => set({ workFilter: f }),

        // memory
        addFact: (groupId = "you") => {
          const fid = "fn" + nextId();
          set((s) => ({
            memGroups: s.memGroups.map((g) =>
              g.id === groupId
                ? {
                    ...g,
                    facts: [
                      { id: fid, text: "", source: "added by you" },
                      ...g.facts,
                    ],
                  }
                : g
            ),
            editingFactId: fid,
            editingText: "",
          }));
        },
        editFact: (id, text) =>
          set((s) => ({
            memGroups: s.memGroups.map((g) => ({
              ...g,
              facts: g.facts.map((f) =>
                f.id === id ? { ...f, text } : f
              ),
            })),
          })),
        deleteFact: (id) =>
          set((s) => ({
            memGroups: s.memGroups.map((g) => ({
              ...g,
              facts: g.facts.filter((f) => f.id !== id),
            })),
          })),
        startEditFact: (id) => {
          const all = get().memGroups.flatMap((g) => g.facts);
          const f = all.find((x) => x.id === id);
          set({ editingFactId: id, editingText: f?.text ?? "" });
        },
        cancelEdit: () => set({ editingFactId: null, editingText: "" }),
        setEditText: (v) => set({ editingText: v }),
        saveFact: (groupId, factId) => {
          const t = get().editingText.trim();
          set((s) => ({
            editingFactId: null,
            editingText: "",
            memGroups: s.memGroups.map((g) =>
              g.id === groupId
                ? {
                    ...g,
                    facts: g.facts.map((f) =>
                      f.id === factId ? { ...f, text: t || f.text } : f
                    ),
                  }
                : g
            ),
          }));
          toast("Saved — Hachi remembers it", "success");
        },
        delFact: (groupId, factId) => {
          set((s) => ({
            memGroups: s.memGroups.map((g) =>
              g.id === groupId
                ? { ...g, facts: g.facts.filter((f) => f.id !== factId) }
                : g
            ),
          }));
          toast("Forgotten — Hachi removed that fact", "success");
        },
        approveProposed: () => {
          set({ proposed: null });
          toast("Updated — Hachi remembers the new version", "success");
        },
        rejectProposed: () => {
          set({ proposed: null });
          toast("Kept as-is — Hachi won't change it", "success");
        },

        // skills / kennel
        addSkill: (kennelId) => {
          const k = get().kennel.find((x) => x.id === kennelId);
          if (k) set({ kennelConsent: k });
        },
        askKennel: (item) => set({ kennelConsent: item }),
        confirmKennel: () => {
          const k = get().kennelConsent;
          if (!k) return;
          set((s) => ({
            kennelConsent: null,
            skills: [
              ...s.skills,
              {
                id: "kn" + nextId(),
                icon: k.icon,
                name: k.name,
                desc: k.desc,
                uses: "0 times",
                lastUsed: "just added",
                stale: false,
              },
            ],
          }));
          toast("Added " + k.name + " — it's ready to use", "success");
        },
        cancelKennel: () => set({ kennelConsent: null }),
        removeSkill: (id) => {
          set((s) => ({ skills: s.skills.filter((k) => k.id !== id) }));
          toast("Removed — one fewer thing on Hachi's plate", "success");
        },

        // OG explorer
        setOgSel: (id) => set({ ogSel: id }),
        setOgView: (v) => set({ ogView: v }),
        setOgHover: (_id) => {
          // local-only hover not stored in global state per contract guidance
          // screens should use local useState for hover; this is a no-op shim
        },

        // authoring
        selectAuthTarget: (name) => set({ authSel: name }),
        setAuthMode: (m) => set({ authMode: m }),
        applyPatch: () => {
          const sel = get().authSel;
          set((s) => ({
            authApplied: true,
            authTargets: s.authTargets.map((t) =>
              t.name === sel ? { ...t, dirty: false } : t
            ),
          }));
          toast("Patch applied — re-indexed in 0.3s", "success");
        },
        revertPatch: () => {
          const sel = get().authSel;
          set((s) => ({
            authApplied: false,
            authTargets: s.authTargets.map((t) =>
              t.name === sel ? { ...t, dirty: true } : t
            ),
          }));
        },
        rejectPatch: () => {
          toast("Rejected — nothing was written", "success");
        },

        // integrations
        connectIntegration: (id) => {
          set((s) => ({
            integrations: s.integrations.map((i) => {
              if (i.id !== id) return i;
              if (i.status === "connected") return i;
              if (i.status === "degraded")
                return {
                  ...i,
                  status: "connected" as const,
                  risky: false,
                  can: i.can.replace(" — broad access", ""),
                };
              return {
                ...i,
                status: "connected" as const,
                can:
                  i.name === "Notion" ? "read & update pages" : i.can,
              };
            }),
          }));
          toast("Connected — Hachi can reach it now", "success");
        },
        disconnectIntegration: (id) => {
          set((s) => ({
            integrations: s.integrations.map((i) =>
              i.id === id
                ? { ...i, status: "none" as const, can: "not connected", risky: false }
                : i
            ),
          }));
          toast("Disconnected", "success");
        },

        // settings
        setSafety: (level) => set({ safety: level }),
        toggleAskRule: (id) =>
          set((s) => ({
            askRules: s.askRules.map((r) =>
              r.id === id ? { ...r, on: !r.on } : r
            ),
          })),
        removeFolder: (f) =>
          set((s) => ({
            allowedFolders: s.allowedFolders.filter((x) => x !== f),
          })),
        removeCommand: (c) =>
          set((s) => ({
            allowedCommands: s.allowedCommands.filter((x) => x !== c),
          })),
        addFolder: () => {
          set((s) => ({ allowedFolders: [...s.allowedFolders, "~/new-folder"] }));
          toast("Added — edit the path in your config", "success");
        },
        addCommand: () =>
          set((s) => ({ allowedCommands: [...s.allowedCommands, "cmd"] })),
        toggleOpTool: (id) =>
          set((s) => ({
            opTools: s.opTools.map((t) =>
              t.id === id ? { ...t, on: !t.on } : t
            ),
          })),
        setProvider: (p) =>
          set((s) => ({ provider: { ...s.provider, ...p } })),

        // chat
        setComposer: (v) =>
          set({ composer: v, slashOpen: v.trim().startsWith("/") }),
        setHomeInput: (v) => set({ homeInput: v }),

        startTask: (text) => {
          const trimmed = text.trim();
          if (!trimmed || get().streaming) return;
          const youId = nextId();
          const hachiId = nextId();

          // Shared state update: always append messages and set streaming=true
          // regardless of mode.  The live/mock divergence happens AFTER this.
          set((s) => ({
            composer: "",
            slashOpen: false,
            streaming: true,
            inContext: null,
            homeInput: "",
            messages: [
              ...s.messages,
              {
                id: youId,
                role: "you" as const,
                text: trimmed,
                cards: [],
                routine: null,
              },
              {
                id: hachiId,
                role: "hachi" as const,
                text: "",
                cards: [],
                routine: null,
              },
            ],
          }));

          // Live mode: delegate to chat.ts via dynamic import to avoid a
          // static circular dependency (store → chat → store).
          if (get().dataMode === "live") {
            void import("../data/domains/chat").then(({ startTaskLive }) => {
              void startTaskLive(trimmed, hachiId);
            });
            return;
          }

          // ── Mock mode: keep existing simulated implementation ──────────────
          const isRoutine =
            /\b(every|each|daily|weekday|weekly|at \d|schedule|routine)\b/i.test(
              trimmed
            );
          if (isRoutine) {
            // routine script
            stream(
              hachiId,
              "Here's what I'd set up. Take a look before it goes on watch — nothing runs until you say so.",
              () => {
                setRoutine(hachiId, {
                  title: "Morning inbox triage",
                  trigger: "Every weekday at 8:00",
                  steps: [
                    "Read your inbox",
                    "Pick out anything urgent",
                    "Message you the urgent ones",
                  ],
                  needs: ["read your inbox", "message you"],
                  costPerRun: 0.04,
                  dryRunDone: false,
                  created: false,
                });
                set({ streaming: false });
              }
            );
          } else {
            // standard script
            stream(
              hachiId,
              "On it. Let me take a quick look at your inbox first.",
              () => {
                addCard(hachiId, {
                  key: "inbox",
                  icon: "mail",
                  title: "Searched your inbox",
                  status: "done",
                  cost: 0.01,
                  tool: "gmail.search(query='is:unread', max=20)",
                  detail:
                    "12 unread · 3 look urgent.\nTop: Priya — \"Q3 deck feedback?\" (2 days ago)",
                  expanded: false,
                });
                setTimeout(() => {
                  stream(
                    hachiId,
                    " Three look urgent — the clearest is Priya waiting on the Q3 deck. I can send her a short reply. Want me to?",
                    () => {
                      set({
                        inContext: {
                          msgId: hachiId,
                          request: "Hachi wants to send an email to Priya",
                          action:
                            "gmail.send to: priya@studio.co · Re: Q3 deck feedback",
                          why: "she's been waiting two days for your reply",
                        },
                      });
                    }
                  );
                }, 450);
              }
            );
          }
        },

        sendMessage: (text) => get().startTask(text),

        stopStreaming: () => {
          // Live mode: abort the SSE stream via chat.ts
          if (get().dataMode === "live") {
            void import("../data/domains/chat").then(({ stopStreamingLive }) => {
              stopStreamingLive();
            });
            set({ streaming: false, inContext: null });
            toast("Stopped — Hachi is resting", "success");
            return;
          }

          // ── Mock mode: keep existing simulated implementation ──────────────
          clearIv();
          const last = [...get().messages].reverse().find((m) => m.role === "hachi");
          set({ streaming: false, inContext: null });
          if (last) setText(last.id, getText(last.id) + " …stopped.");
          toast("Stopped — Hachi is resting", "success");
        },

        icAllow: () => {
          // Live mode: clear leash and let the SSE stream continue
          if (get().dataMode === "live") {
            void import("../data/domains/chat").then(({ allowLeashLive }) => {
              void allowLeashLive();
            });
            return;
          }

          // ── Mock mode: keep existing simulated implementation ──────────────
          const ic = get().inContext;
          if (!ic) return;
          const id = ic.msgId;
          set({ inContext: null });
          addCard(id, {
            key: "send",
            icon: "send",
            title: "Sending email to Priya",
            status: "working",
            cost: 0.0,
            tool: "gmail.send(to='priya@studio.co')",
            detail:
              "To: priya@studio.co\nRe: Q3 deck feedback\n\"Here's the latest — your notes are folded in. Happy to walk through it tomorrow.\"",
            expanded: false,
          });
          setTimeout(() => {
            setCard(id, "send", { status: "done", title: "Sent email to Priya", cost: 0.02 });
            stream(
              id,
              " Done — your reply is on its way. Anything else?",
              () => {
                set((s) => ({
                  streaming: false,
                  taskSpent:
                    Math.round((s.taskSpent + 0.02) * 100) / 100,
                  spent:
                    Math.round((s.spent + 0.02) * 100) / 100,
                }));
                toast("Sent — Hachi is resting", "success");
              }
            );
          }, 1500);
        },

        icDeny: () => {
          // Live mode: abort stream (user declined permission)
          if (get().dataMode === "live") {
            void import("../data/domains/chat").then(({ denyLeashLive }) => {
              void denyLeashLive();
            });
            toast("Stayed put — Hachi is resting", "success");
            return;
          }

          // ── Mock mode: keep existing simulated implementation ──────────────
          const ic = get().inContext;
          if (!ic) return;
          const id = ic.msgId;
          set({ inContext: null });
          stream(
            id,
            " No problem — I'll leave it. What else can I do?",
            () => set({ streaming: false })
          );
          toast("Stayed put — Hachi is resting", "success");
        },

        toggleCard: (mid, key) => {
          set((s) => ({
            messages: s.messages.map((m) =>
              m.id === mid
                ? {
                    ...m,
                    cards: m.cards.map((c) =>
                      c.key === key ? { ...c, expanded: !c.expanded } : c
                    ),
                  }
                : m
            ),
          }));
        },

        dryRunRoutine: (msgId) => {
          if (get().streaming) return;
          set({ streaming: true });
          addCard(msgId, {
            key: "dry",
            icon: "play",
            title: "Dry run — nothing sent",
            status: "working",
            cost: 0.0,
            tool: "scheduler.dry_run(routine='inbox_triage')",
            detail:
              "Ran the routine once against today's inbox, in a safe sandbox. Nothing left your machine.",
            expanded: false,
          });
          setTimeout(() => {
            setCard(msgId, "dry", { status: "done", cost: 0.03 });
            setRoutine(msgId, { dryRunDone: true });
            stream(
              msgId,
              " Dry run looks good — it found 3 urgent emails and drafted the briefing. Ready to set it live?",
              () =>
                set((s) => ({
                  streaming: false,
                  taskSpent:
                    Math.round((s.taskSpent + 0.03) * 100) / 100,
                  spent:
                    Math.round((s.spent + 0.03) * 100) / 100,
                }))
            );
          }, 1600);
        },

        createRoutine: (msgId) => {
          if (get().streaming) return;
          const m = get().messages.find((x) => x.id === msgId);
          const ro = m?.routine ?? {};
          set((s) => ({
            streaming: true,
            routines: [
              {
                id: nextId(),
                title: (ro as Record<string,unknown>).title as string || "New routine",
                schedule: "weekdays 8:00",
                next: "tomorrow 8:00",
                cost: ((ro as Record<string,unknown>).costPerRun as number) || 0.04,
                paused: false,
                steps: ((ro as Record<string,unknown>).steps as string[] || []).map((l: string) => ({
                  label: l,
                  state: "done" as const,
                })),
                runHistory: [],
              },
              ...s.routines,
            ],
            messages: s.messages.map((msg) =>
              msg.id === msgId
                ? { ...msg, routine: { ...(msg.routine || {}), created: true } }
                : msg
            ),
          }));
          stream(
            msgId,
            " Done — it's on watch under your routines. I'll run it tomorrow at 8:00.",
            () => set({ streaming: false })
          );
          toast("Added — the routine is on watch", "success");
        },

        // live run
        openRun: (runId, from = "home") => {
          clearRunIv();
          _runFrom = from;
          const s = get();
          const r =
            (runId != null && s.runs.find((x) => x.id === runId)) ||
            s.runs[0];
          if (!r) {
            toast("Nothing live right now — Hachi is on watch", "success");
            return;
          }
          set({
            runView: r,
            runLines: [
              { text: "hachi ▸ run " + slug(r.title) + " --watch" },
              { text: "→ starting on your machine", tone: "muted" },
            ],
            runStreaming: true,
            runDone: false,
            runLeash: null,
            runSpent: r.spent || 0,
            runPack: [
              { id: "p1", name: "reader-1", role: "reader", task: "reading PR #482 · auth refactor", status: "working", tokens: 6240 },
              { id: "p2", name: "reader-2", role: "reader", task: "reading PR #485 · vitest migration", status: "working", tokens: 5180 },
              { id: "p3", name: "writer", role: "writer", task: "waiting for the readers", status: "pending", tokens: 0 },
            ],
            runSteps: (
              r.steps && r.steps.length
                ? r.steps
                : [
                    { label: "Read the source", state: "working" },
                    { label: "Do the work", state: "pending" },
                    { label: "Report back to you", state: "pending" },
                  ]
            ).map((step) => ({ ...step })),
          });
          _runQueue = [
            { line: { text: "✓ cloned repo index · 1,204 files", tone: "success" }, steps: [{ i: 0, state: "done" }] },
            { line: { text: "→ spawning the pack · 3 sub-agents", tone: "muted" }, steps: [{ i: 1, state: "working" }] },
            { line: { text: "· reader-1 → PR #482 auth refactor", tone: "muted" } },
            { line: { text: "· reader-2 → PR #485 vitest migration", tone: "muted" } },
            { line: { text: "✓ reader-1 done — 2 risks noted", tone: "success" }, pack: { id: "p1", status: "done", task: "PR #482 · 2 risks noted" }, spend: 0.01 },
            { line: { text: "✓ reader-2 done — looks clean", tone: "success" }, pack: { id: "p2", status: "done", task: "PR #485 · looks clean" }, spend: 0.01 },
            { line: { text: "→ writer drafting the summary", tone: "muted" }, pack: { id: "p3", status: "working", task: "drafting the summary" }, steps: [{ i: 1, state: "done" }, { i: 2, state: "working" }] },
            { line: { text: "✓ summary ready — 6 PRs · 2 need review", tone: "success" }, pack: { id: "p3", status: "done", task: "summary drafted" }, steps: [{ i: 2, state: "done" }], spend: 0.01 },
            { leash: { request: "Hachi wants to post the summary", action: "slack.post #eng · \"This week: 6 PRs, 2 need review\"", why: "share the weekly PR summary with your team" }, steps: [{ i: 3, state: "working" }] },
            { line: { text: "✓ posted to #eng", tone: "success" } },
          ];
          _runStart = Date.now();
          _runIv = setInterval(() => runTick(), 1100);
        },

        watchLive: (runId) => get().openRun(runId, "work"),

        closeRun: () => {
          clearRunIv();
          _runQueue = [];
          set({ runView: null, runLeash: null });
        },

        runAllowLeash: () => {
          set((s) => ({
            runLeash: null,
            runStreaming: true,
            runLines: [...s.runLines, { text: "✓ allowed — posting now", tone: "success" }],
          }));
          clearRunIv();
          _runIv = setInterval(() => runTick(), 1100);
          toast("On it — Hachi is working", "working");
        },

        runDenyLeash: () => {
          clearRunIv();
          _runQueue = [];
          const s = get();
          set({
            ...archiveRun(),
            runLeash: null,
            runStreaming: false,
            runDone: true,
            runLines: [
              ...s.runLines,
              { text: "✗ denied — Hachi is resting", tone: "danger" },
            ],
            runPack: s.runPack.map((p) => ({
              ...p,
              status: p.status === "working" ? "done" as const : p.status,
            })),
          });
          toast("Stayed put — Hachi is resting", "success");
        },

        stopRun: () => {
          clearRunIv();
          _runQueue = [];
          const s = get();
          set({
            ...archiveRun(),
            runStreaming: false,
            runDone: true,
            runLeash: null,
            runLines: [
              ...s.runLines,
              { text: "■ stopped — Hachi is resting", tone: "muted" },
            ],
            runPack: s.runPack.map((p) => ({
              ...p,
              status: p.status === "working" ? "done" as const : p.status,
            })),
          });
          toast("Stopped — Hachi is resting", "success");
        },

        simulateReconnect: () => {
          set({ reconnecting: true, palette: { open: false, query: "" } });
          if (_rc) clearTimeout(_rc);
          _rc = setTimeout(() => {
            set({ reconnecting: false });
            toast("Reconnected — Hachi is back", "success");
          }, 3800);
        },
      };
    },
    {
      name: "hachi-store",
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        operator: state.operator,
        theme: state.theme,
      }),
    }
  )
);
