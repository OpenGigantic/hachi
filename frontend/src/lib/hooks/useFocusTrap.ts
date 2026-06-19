// useFocusTrap — confines keyboard focus inside a container while active.
//
// Usage:
//   const trapRef = useFocusTrap(isOpen);
//   <div ref={trapRef} role="dialog" aria-modal="true">…</div>
//
// Behaviour:
//   • Focuses the first focusable child when `active` becomes true.
//   • Tab / Shift-Tab cycles within the container (wraps at edges).
//   • On deactivation the ref loses the event listener automatically.
//   • Returns focus to the element that was active *before* the trap opened.

import { useEffect, useRef } from 'react';

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

export function useFocusTrap<T extends HTMLElement = HTMLElement>(active: boolean) {
  const containerRef = useRef<T>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);

  useEffect(() => {
    if (!active) return;

    // Remember what had focus before we opened.
    previouslyFocused.current = document.activeElement as HTMLElement | null;

    const el = containerRef.current;
    if (!el) return;

    // Move focus into the dialog immediately.
    const focusables = Array.from(el.querySelectorAll<HTMLElement>(FOCUSABLE));
    if (focusables.length > 0) {
      focusables[0].focus();
    } else {
      // Fall back to the container itself so Escape keydown still works.
      el.setAttribute('tabindex', '-1');
      el.focus();
    }

    function onKeyDown(e: KeyboardEvent) {
      if (e.key !== 'Tab') return;
      const focusableNow = Array.from(el!.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (focusableNow.length === 0) return;

      const first = focusableNow[0];
      const last = focusableNow[focusableNow.length - 1];

      if (e.shiftKey) {
        if (document.activeElement === first) {
          e.preventDefault();
          last.focus();
        }
      } else {
        if (document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    }

    el.addEventListener('keydown', onKeyDown);
    return () => {
      el.removeEventListener('keydown', onKeyDown);
    };
  }, [active]);

  // Restore focus when the trap is deactivated.
  useEffect(() => {
    if (!active && previouslyFocused.current) {
      previouslyFocused.current.focus();
      previouslyFocused.current = null;
    }
  }, [active]);

  return containerRef;
}
