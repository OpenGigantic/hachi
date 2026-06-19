// ============================================================
// Hachi Web App — useCountdown hook
// Ticks every 1s, formats "1m 19s" / "45s", clamps at 0.
// Expired approvals auto-decline via the store tickApprovals.
// ============================================================

import { useState, useEffect, useRef } from "react";
import { useStore } from "../store/store";

interface CountdownResult {
  ms: number;
  label: string;
}

function fmt(ms: number): string {
  const s = Math.max(0, Math.ceil(ms / 1000));
  const mm = Math.floor(s / 60);
  const ss = s % 60;
  if (mm > 0) {
    return mm + "m " + (ss < 10 ? "0" : "") + ss + "s";
  }
  return ss + "s";
}

/**
 * Countdown hook. Returns live `ms` remaining and a formatted `label`.
 * When `expiresAt` is in the past, clamps to 0 and label is "0s".
 *
 * A single shared interval (via the store's tickApprovals) drives
 * auto-decline; this hook only drives per-card display.
 */
export function useCountdown(expiresAt: number): CountdownResult {
  const tickApprovals = useStore((s) => s.tickApprovals);
  const tickRef = useRef(tickApprovals);
  tickRef.current = tickApprovals;

  const [ms, setMs] = useState<number>(() =>
    Math.max(0, expiresAt - Date.now())
  );

  useEffect(() => {
    // Immediately sync on mount / when expiresAt changes
    setMs(Math.max(0, expiresAt - Date.now()));

    const id = setInterval(() => {
      const remaining = Math.max(0, expiresAt - Date.now());
      setMs(remaining);
      // Trigger global auto-decline check on every tick
      tickRef.current();
    }, 1000);

    return () => clearInterval(id);
  }, [expiresAt]);

  return { ms, label: fmt(ms) };
}
