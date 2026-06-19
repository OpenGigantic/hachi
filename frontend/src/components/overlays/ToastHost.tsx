// ============================================================
// ToastHost — renders the global transient toast pill.
// Gates on store.toast; auto-dismiss is handled in store.showToast
// (3200ms timeout). Reuses ui/Toast for the pill itself.
// Prototype markup: Hachi Web App.dc.html lines 1461–1466
// ============================================================

import React, { CSSProperties } from 'react';
import { useStore } from '../../lib/store/store';
import { Toast } from '../ui/Toast';
import type { ToastTone } from '../ui/Toast';

export function ToastHost() {
  const toast = useStore((s) => s.toast);

  if (!toast) return null;

  // Map store tone values ("working" / "success" / "blocked" / "info")
  // to the Toast component's accepted tones.
  const toneMap: Record<string, ToastTone> = {
    success: 'success',
    working: 'info',
    blocked: 'blocked',
    info:    'info',
  };
  const tone: ToastTone = toneMap[toast.tone ?? 'success'] ?? 'success';

  const wrapStyle: CSSProperties = {
    position: 'fixed',
    bottom: 28,
    left: '50%',
    transform: 'translateX(-50%)',
    zIndex: 70,
  };

  return (
    <div style={wrapStyle} aria-live="polite">
      <Toast message={toast.message} tone={tone} />
    </div>
  );
}
