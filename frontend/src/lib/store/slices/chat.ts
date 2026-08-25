// ============================================================
// T-63 — store slice: chat / composer (concern #19 of 22, last to move).
// Moved verbatim from frontend/src/lib/store/store.ts; the module-scope
// `toast(...)` helper calls become `get().showToast(...)` since the toasts
// slice now owns that action. Cross-slice writes (taskSpent/spent owned by
// the budget slice, routines owned by the work-queue slice) go through the
// Store-typed `set`, exactly as they did before the split.
//
// This file imports allowLeashLive/denyLeashLive/startTaskLive/
// stopStreamingLive directly from the chat domain (rather than the
// module-scope import store.ts used to carry) -- that import already
// participates in a tolerated store<->chat module cycle (chat.ts imports
// useStore). Verified with `npm run build` (tsc --noEmit does not see TDZ
// issues from module-init order; Rollup does) -- see this task's report.
// This is the LAST slice moved, per the task's explicit ordering (highest
// dataMode-fork density + the cycle risk).
// ============================================================
import type { StateCreator } from "zustand";
import {
  allowLeashLive,
  denyLeashLive,
  startTaskLive,
  stopStreamingLive,
} from "../../data/domains/chat";
import { apiSend } from "../../data/client";
import type { ActionCard, ChatMessage, InContextLeash, PendingAttachment } from "../../data/types";
import type { Store } from "../store";
import { capMessages, nextId } from "./shared";

export interface ChatState {
  slashOpen: boolean;
  streaming: boolean;
  inContext: InContextLeash | null;
  messages: ChatMessage[];
  composer: string;
  pendingAttachments: PendingAttachment[];
}

export interface ChatActions {
  startTask: (text: string) => void;
  /** Retry the last failed turn IN PLACE — re-sends the last prompt and reuses the
   *  failed assistant bubble instead of appending a duplicate "you" message. */
  retryLastMessage: () => void;
  sendMessage: (text: string) => void;
  stopStreaming: () => void;
  setComposer: (v: string) => void;
  addPendingAttachment: (a: PendingAttachment) => void;
  updatePendingAttachment: (localId: string, patch: Partial<PendingAttachment>) => void;
  removePendingAttachment: (fileId: string) => void;
  clearPendingAttachments: () => void;
  setHomeInput: (v: string) => void;
  icAllow: () => void;
  icAlwaysAllow: () => void;
  icDeny: () => void;
  toggleCard: (mid: number, key: string) => void;
  dryRunRoutine: (msgId: number) => void;
  createRoutine: (msgId: number) => void;
}

// module-level timer, private to this slice (moved verbatim from store.ts)
let _iv: ReturnType<typeof setInterval> | null = null;
function clearIv() { if (_iv) { clearInterval(_iv); _iv = null; } }

export const createChatSlice: StateCreator<Store, [], [], ChatState & ChatActions> = (
  set,
  get
) => {
  // ---- helpers ----
  const getText = (id: number): string => {
    const m = get().messages.find((x) => x.id === id);
    return m ? m.text : "";
  };

  const setText = (id: number, t: string) => {
    set((s) => ({
      messages: capMessages(s.messages.map((m) => (m.id === id ? { ...m, text: t } : m))),
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

  return {
    slashOpen: false,
    streaming: false,
    inContext: null,
    messages: [],
    composer: "",
    pendingAttachments: [],

    setComposer: (v) =>
      set({ composer: v, slashOpen: v.trim().startsWith("/") }),
    setHomeInput: (v) => set({ homeInput: v }),
    addPendingAttachment: (a) =>
      set((s) => ({ pendingAttachments: [...s.pendingAttachments, a] })),
    updatePendingAttachment: (localId, patch) =>
      set((s) => ({
        pendingAttachments: s.pendingAttachments.map((a) =>
          a.fileId === localId ? { ...a, ...patch } : a
        ),
      })),
    removePendingAttachment: (fileId) =>
      set((s) => ({
        pendingAttachments: s.pendingAttachments.filter((a) => a.fileId !== fileId),
      })),
    clearPendingAttachments: () => set({ pendingAttachments: [] }),

    startTask: (text) => {
      const trimmed = text.trim();
      const pending = get().pendingAttachments;
      if (!trimmed && pending.length === 0) return;
      if (get().streaming) return;
      const youId = nextId();
      const hachiId = nextId();
      const attachmentSnapshot = [...pending];
      // Only include attachments that finished uploading successfully.
      const attachmentIds = attachmentSnapshot
        .filter((a) => !a.uploading && !a.error)
        .map((a) => a.fileId);

      // Shared state update: always append messages and set streaming=true
      // regardless of mode.  The live/mock divergence happens AFTER this.
      set((s) => ({
        composer: "",
        slashOpen: false,
        streaming: true,
        inContext: null,
        homeInput: "",
        pendingAttachments: [],
        messages: capMessages([
          ...s.messages,
          {
            id: youId,
            role: "you" as const,
            text: trimmed,
            cards: [],
            routine: null,
            ts: Date.now(),
            attachments: attachmentSnapshot.map((a) => ({
              name: a.name,
              mediaType: a.mediaType,
              size: a.size,
            })),
          },
          {
            id: hachiId,
            role: "hachi" as const,
            text: "",
            cards: [],
            routine: null,
            ts: Date.now(),
          },
        ]),
      }));

      // Live mode: delegate to the chat domain's startTaskLive, imported
      // statically at the top of this file. The store<->chat cycle this
      // creates is real and deliberately documented, not avoided -- see
      // data/domains/chat.ts's header comment and the guard test
      // module-graph.test.ts, which pins the cycle so this comment can't drift again.
      if (get().dataMode === "live") {
        startTaskLive(trimmed, hachiId, attachmentIds).catch(() => {
          // Catch errors from the live send path: reset streaming and surface an error.
          set((s) => ({
            streaming: false,
            messages: s.messages.map((m) =>
              m.id === hachiId
                ? { ...m, text: m.text || "Something went wrong — please try again.", error: true }
                : m
            ),
          }));
          get().showToast("Send failed — please try again", "blocked");
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
                      // Mock-mode fixture — never sent to a gateway, so there is
                      // no real request_id to carry.
                      requestId: "",
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

    retryLastMessage: () => {
      // Find the last user prompt and the trailing (failed) assistant bubble.
      const msgs = get().messages;
      let youText: string | null = null;
      let hachiId: number | null = null;
      for (let i = msgs.length - 1; i >= 0; i -= 1) {
        if (hachiId === null && msgs[i].role === "hachi") hachiId = msgs[i].id;
        if (msgs[i].role === "you") {
          youText = msgs[i].text;
          break;
        }
      }
      if (!youText || hachiId === null) return;
      const retryHachiId = hachiId;
      // Reset the failed assistant bubble IN PLACE — appending a new you/hachi pair
      // was the source of the duplicated user message on retry.
      set((s) => ({
        streaming: true,
        inContext: null,
        messages: s.messages.map((m) =>
          m.id === retryHachiId ? { ...m, text: "", cards: [] } : m
        ),
      }));
      if (get().dataMode === "live") {
        startTaskLive(youText, retryHachiId).catch(() => {
          set((s) => ({
            streaming: false,
            messages: s.messages.map((m) =>
              m.id === retryHachiId
                ? { ...m, text: m.text || "Something went wrong — please try again.", error: true }
                : m
            ),
          }));
          get().showToast("Send failed — please try again", "blocked");
        });
      } else {
        // Mock mode has no live gateway to resend against — just clear the error.
        set({ streaming: false });
      }
    },

    sendMessage: (text) => get().startTask(text),

    stopStreaming: () => {
      // Live mode: abort the SSE stream via chat.ts
      if (get().dataMode === "live") {
        stopStreamingLive();
        set({ streaming: false, inContext: null });
        get().showToast("Stopped — Hachi is resting", "success");
        return;
      }

      // ── Mock mode: keep existing simulated implementation ──────────────
      clearIv();
      const last = [...get().messages].reverse().find((m) => m.role === "hachi");
      set({ streaming: false, inContext: null });
      if (last) setText(last.id, getText(last.id) + " …stopped.");
      get().showToast("Stopped — Hachi is resting", "success");
    },

    icAllow: () => {
      // Live mode: clear leash and let the SSE stream continue
      if (get().dataMode === "live") {
        void allowLeashLive();
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
            get().showToast("Sent — Hachi is resting", "success");
          }
        );
      }, 1500);
    },

    icAlwaysAllow: () => {
      // Always-allow: persist to gateway via POST /sessions/{id}/permissions/always-allow
      // then fall through to the same optimistic path as icAllow.
      const _ic = get().inContext;
      if (!_ic) return;

      if (get().dataMode === "live") {
        // Persist always-allow rule to the gateway; surface failure honestly.
        void (async () => {
          try {
            await apiSend("POST", `/sessions/current/permissions/always-allow`, {
              request: _ic.request,
              action: _ic.action,
            });
            // On success, let the live stream continue as "allow once" would
            void allowLeashLive();
            get().showToast("Always allowed — Hachi won't ask again for this", "success");
          } catch {
            // Endpoint returned an error — tell the user, don't silently proceed
            get().showToast("Could not save always-allow rule — please try again", "blocked");
          }
        })();
        return;
      }

      // Mock mode: same visual path as icAllow + show a different toast
      get().icAllow();
      get().showToast("Always allowed — Hachi won't ask again for this", "success");
    },

    icDeny: () => {
      // Live mode: abort stream (user declined permission)
      if (get().dataMode === "live") {
        void denyLeashLive();
        get().showToast("Stayed put — Hachi is resting", "success");
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
      get().showToast("Stayed put — Hachi is resting", "success");
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
      get().showToast("Added — the routine is on watch", "success");
    },
  };
};
