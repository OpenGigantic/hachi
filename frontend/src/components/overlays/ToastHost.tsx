// ============================================================
// ToastHost — renders the global transient toast pill.
// The wrapper is ALWAYS mounted (not gated on store.toast) so its
// aria-live region exists in the DOM before content ever changes —
// many screen readers won't announce a live region that appears
// with its text already inside it. Auto-dismiss lives here (not in
// store.showToast) so it can pause while the toast is hovered and
// use a longer duration for error/"blocked" tones.
// Prototype markup: Hachi Web App.dc.html lines 1461–1466
// ============================================================

import React, { CSSProperties, useEffect, useState } from 'react';
import { useStore } from '../../lib/store/store';
import { Toast } from '../ui/Toast';
import type { ToastTone } from '../ui/Toast';

const DEFAULT_DURATION_MS = 3200;
const ERROR_DURATION_MS = 6500;

// Map store tone values ("working" / "success" / "blocked" / "info")
// to the Toast component's accepted tones.
const TONE_MAP: Record<string, ToastTone> = {
  success: 'success',
  working: 'info',
  blocked: 'blocked',
  info:    'info',
};

export function ToastHost() {
  const toast = useStore((s) => s.toast);
  const dismissToast = useStore((s) => s.dismissToast);
  const [paused, setPaused] = useState(false);

  useEffect(() => {
    if (!toast || paused) return;
    const duration = toast.tone === 'blocked' ? ERROR_DURATION_MS : DEFAULT_DURATION_MS;
    const t = setTimeout(() => dismissToast(), duration);
    return () => clearTimeout(t);
  }, [toast, paused, dismissToast]);

  const wrapStyle: CSSProperties = {
    position: 'fixed',
    bottom: 28,
    left: '50%',
    transform: 'translateX(-50%)',
    zIndex: 70,
  };

  const tone: ToastTone = TONE_MAP[toast?.tone ?? 'success'] ?? 'success';

  return (
    <div style={wrapStyle}>
      {toast && (
        <Toast
          message={toast.message}
          tone={tone}
          onDismiss={dismissToast}
          onMouseEnter={() => setPaused(true)}
          onMouseLeave={() => setPaused(false)}
        />
      )}
    </div>
  );
}
