// ============================================================
// Skills screen — "The library"   route /skills
// Source: prototype lines 800–844 (markup), 2106–2107 (handlers),
//         2320–2325 (renderVals)
// ============================================================

import { useState, useEffect, useRef } from 'react';
import { createFileRoute } from '@tanstack/react-router';
import { useStore } from '../lib/store/store';
import { Icon } from '../components/icons/Icon';
import type { Skill, KennelItem } from '../lib/data/types';
import { loadSkills, loadKennel } from '../lib/data/domains/skills';

// ---- .og explainer localStorage key ----
const OG_EXPLAINER_KEY = 'hachi-og-explainer-dismissed';

// ---- .og explainer panel (E6-T1) -----------------------------------

function OgExplainerPanel() {
  const [dismissed, setDismissed] = useState(() =>
    typeof localStorage !== 'undefined' && localStorage.getItem(OG_EXPLAINER_KEY) === '1'
  );

  function dismiss() {
    localStorage.setItem(OG_EXPLAINER_KEY, '1');
    setDismissed(true);
  }

  if (dismissed) return null;

  return (
    <div
      style={{
        background: 'var(--hachi-trust-tint)',
        border: '0.5px solid var(--hachi-trust)',
        borderRadius: 14,
        padding: '16px 18px',
        display: 'flex',
        gap: 14,
        alignItems: 'flex-start',
      }}
      role="note"
      aria-label="What is a .og skill file"
    >
      <span style={{ color: 'var(--hachi-trust)', display: 'inline-flex', flexShrink: 0, marginTop: 1 }}>
        <Icon name="file" size={18} />
      </span>
      <div style={{ flex: 1 }}>
        <div style={{ fontWeight: 500, fontSize: 14, color: 'var(--hachi-ink)', marginBottom: 4 }}>
          What is a .og skill file?
        </div>
        <div style={{ fontSize: 13, color: 'var(--hachi-muted)', lineHeight: '20px' }}>
          Skills are stored as <code style={{ fontFamily: 'var(--font-mono)', background: 'var(--hachi-cream)', padding: '1px 5px', borderRadius: 4 }}>.og</code> (ObjectGraph) files. Hachi reads them in three passes — first a short routing index, then summaries, then full content only when needed — so it uses far fewer tokens than loading a whole document. You can browse, add, and remove skills from the kennel below.
        </div>
      </div>
      <button
        type="button"
        onClick={dismiss}
        aria-label="Dismiss .og explainer"
        style={{
          border: 'none',
          background: 'transparent',
          color: 'var(--hachi-muted)',
          cursor: 'pointer',
          display: 'inline-flex',
          padding: 4,
          borderRadius: 6,
          flexShrink: 0,
        }}
        onMouseEnter={(e) => {
          ;(e.currentTarget as HTMLButtonElement).style.background = 'var(--hachi-cream)'
        }}
        onMouseLeave={(e) => {
          ;(e.currentTarget as HTMLButtonElement).style.background = 'transparent'
        }}
      >
        <Icon name="x" size={15} />
      </button>
    </div>
  );
}

// ---- local hover-state helpers (not in store) ----------------
function SkillCard({ sk, operator }: { sk: Skill; operator: boolean }) {
  const removeSkill = useStore((s) => s.removeSkill);
  const [editHover, setEditHover] = useState(false);
  const [removeHover, setRemoveHover] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const confirmTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => { if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current); }, []);
  function handleRemoveClick() {
    if (!confirmRemove) {
      setConfirmRemove(true);
      confirmTimerRef.current = setTimeout(() => setConfirmRemove(false), 4000);
      return;
    }
    if (confirmTimerRef.current) clearTimeout(confirmTimerRef.current);
    setConfirmRemove(false);
    removeSkill(sk.id);
  }

  return (
    <div
      style={{
        background: 'var(--hachi-surface)',
        border: '0.5px solid var(--hachi-line)',
        borderRadius: 14,
        padding: 16,
        display: 'flex',
        flexDirection: 'column',
        gap: 9,
      }}
    >
      {/* header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
        <span style={{ color: 'var(--hachi-ink)', display: 'inline-flex' }}>
          <Icon name={sk.icon} size={18} />
        </span>
        <span style={{ fontWeight: 500, fontSize: 15, flex: 1 }}>{sk.name}</span>
        {operator && (
          <button
            type="button"
            disabled
            aria-disabled="true"
            title="Skill editing is coming soon"
            onMouseEnter={() => setEditHover(true)}
            onMouseLeave={() => setEditHover(false)}
            style={{
              border: 'none',
              background: editHover ? 'var(--hachi-cream)' : 'transparent',
              color: 'var(--hachi-muted)',
              cursor: 'not-allowed',
              opacity: 0.5,
              display: 'inline-flex',
              padding: 4,
              borderRadius: 6,
              transition: 'background var(--dur-fast) var(--ease)',
            }}
            aria-label={`Edit ${sk.name} — coming soon`}
          >
            <Icon name="pencil" size={15} />
          </button>
        )}
      </div>

      {/* description */}
      <div style={{ fontSize: 13, color: 'var(--hachi-muted)', lineHeight: '20px' }}>
        {sk.desc}
      </div>

      {/* usage row */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          fontSize: 12,
          color: 'var(--hachi-muted)',
          marginTop: 2,
        }}
      >
        <span>
          used {sk.uses} · {sk.lastUsed}
        </span>
        {sk.stale && (
          <button
            type="button"
            onClick={handleRemoveClick}
            onMouseEnter={() => setRemoveHover(true)}
            onMouseLeave={() => setRemoveHover(false)}
            onBlur={() => setConfirmRemove(false)}
            onKeyDown={(e) => {
              if (e.key === 'Escape' && confirmRemove) { e.stopPropagation(); setConfirmRemove(false); }
            }}
            aria-label={
              confirmRemove
                ? `Confirm remove ${sk.name} skill`
                : `Remove ${sk.name} skill (not used in 90 days)`
            }
            style={{
              border: 'none',
              background: 'transparent',
              color: 'var(--hachi-danger)',
              font: '500 12px var(--font-body)',
              cursor: 'pointer',
              padding: 0,
              textDecoration: removeHover || confirmRemove ? 'underline' : 'none',
              transition: 'text-decoration var(--dur-fast) var(--ease)',
            }}
          >
            {confirmRemove ? 'Click again to confirm removal' : 'not used in 90 days — remove?'}
          </button>
        )}
      </div>
    </div>
  );
}

function KennelCard({ k }: { k: KennelItem }) {
  const addSkill = useStore((s) => s.addSkill);
  const [addHover, setAddHover] = useState(false);

  return (
    <div
      style={{
        background: 'var(--hachi-surface)',
        border: '0.5px solid var(--hachi-line)',
        borderRadius: 14,
        padding: 16,
        display: 'flex',
        flexDirection: 'column',
        gap: 10,
      }}
    >
      {/* header row */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
        <span style={{ color: 'var(--hachi-trust)', display: 'inline-flex' }}>
          <Icon name={k.icon} size={17} />
        </span>
        <span style={{ fontWeight: 500, fontSize: 14 }}>{k.name}</span>
      </div>

      {/* description */}
      <div
        style={{
          fontSize: 12,
          color: 'var(--hachi-muted)',
          lineHeight: '19px',
          flex: 1,
        }}
      >
        {k.desc}
      </div>

      {/* add button */}
      <button
        onClick={() => addSkill(k.id)}
        onMouseEnter={() => setAddHover(true)}
        onMouseLeave={() => setAddHover(false)}
        style={{
          height: 32,
          borderRadius: 10,
          border: '0.5px solid var(--hachi-line)',
          background: addHover ? 'var(--hachi-cream)' : 'var(--hachi-canvas)',
          color: 'var(--hachi-ink)',
          font: '500 13px var(--font-body)',
          cursor: 'pointer',
          transition: 'background var(--dur-fast) var(--ease)',
        }}
      >
        Add
      </button>
    </div>
  );
}

function Skills() {
  const skills = useStore((s) => s.skills);
  const kennel = useStore((s) => s.kennel);
  const operator = useStore((s) => s.operator);
  const openKennelBrowse = useStore((s) => s.openKennelBrowse);

  useEffect(() => { void loadSkills(); void loadKennel(); }, []);

  // "Browse the kennel" header button hover
  const [browseHover, setBrowseHover] = useState(false);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* ---- top bar ---- */}
      <div
        className="hachi-skills-header"
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: 16,
          padding: '0 32px',
          height: 68,
          flexShrink: 0,
          borderBottom: '0.5px solid var(--hachi-line)',
        }}
      >
        <h1
          style={{
            margin: 0,
            fontFamily: 'var(--font-display)',
            fontWeight: 500,
            fontSize: 22,
            lineHeight: '26px',
          }}
        >
          The library
        </h1>
        <span style={{ fontSize: 13, color: 'var(--hachi-muted)' }}>
          what Hachi can do
        </span>
        <div style={{ flex: 1 }} />
        {/* CRIT-003: Browse the kennel — wired to openKennelBrowse */}
        <button
          onClick={openKennelBrowse}
          onMouseEnter={() => setBrowseHover(true)}
          onMouseLeave={() => setBrowseHover(false)}
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 7,
            height: 38,
            padding: '0 14px',
            borderRadius: 10,
            border: '1px solid var(--hachi-trust)',
            background: browseHover ? 'var(--hachi-trust-tint)' : 'transparent',
            color: 'var(--hachi-trust)',
            font: '500 13px var(--font-body)',
            cursor: 'pointer',
            transition: 'background var(--dur-fast) var(--ease)',
          }}
          aria-label="Browse the kennel"
        >
          <Icon name="skills" size={15} />
          Browse the kennel
        </button>
      </div>

      {/* ---- scrollable body ---- */}
      <div
        className="hachi-scroll hachi-skills-body"
        style={{ flex: 1, overflow: 'auto', padding: '24px 32px 64px' }}
      >
        <div
          style={{
            maxWidth: 880,
            margin: '0 auto',
            display: 'flex',
            flexDirection: 'column',
            gap: 26,
          }}
        >
          {/* E6-T1: .og explainer panel (dismissible, localStorage) */}
          <OgExplainerPanel />

          {/* ---- Installed section ---- */}
          <section>
            <h2
              style={{
                margin: '0 0 12px',
                fontFamily: 'var(--font-display)',
                fontWeight: 500,
                fontSize: 15,
                color: 'var(--hachi-muted)',
              }}
            >
              Installed
            </h2>

            {skills.length === 0 ? (
              /* empty state */
              <div
                style={{
                  background: 'var(--hachi-surface)',
                  border: '0.5px solid var(--hachi-line)',
                  borderRadius: 14,
                  padding: '32px 24px',
                  textAlign: 'center',
                  color: 'var(--hachi-muted)',
                  fontSize: 14,
                }}
              >
                No skills installed yet — browse the kennel to add some.
              </div>
            ) : (
              <div
                className="hachi-skills-installed-grid"
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr',
                  gap: 12,
                }}
              >
                {skills.map((sk) => (
                  <SkillCard key={sk.id} sk={sk} operator={operator} />
                ))}
              </div>
            )}
          </section>

          {/* LOW-007: Only render "From the kennel" section when there are items */}
          {kennel.length > 0 && (
            <section>
              <h2
                style={{
                  margin: '0 0 12px',
                  fontFamily: 'var(--font-display)',
                  fontWeight: 500,
                  fontSize: 15,
                  color: 'var(--hachi-muted)',
                }}
              >
                From the kennel
              </h2>
              <div
                className="hachi-skills-kennel-grid"
                style={{
                  display: 'grid',
                  gridTemplateColumns: '1fr 1fr 1fr',
                  gap: 12,
                }}
              >
                {kennel.map((k) => (
                  <KennelCard key={k.id} k={k} />
                ))}
              </div>
            </section>
          )}
        </div>
      </div>
    </div>
  );
}

export const Route = createFileRoute('/skills')({
  component: Skills,
});
