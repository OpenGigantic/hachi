// ============================================================
// Skills screen — "The library"   route /skills
// Source: prototype lines 800–844 (markup), 2106–2107 (handlers),
//         2320–2325 (renderVals)
// ============================================================

import { useState, useEffect } from 'react';
import { createFileRoute } from '@tanstack/react-router';
import { useStore } from '../lib/store/store';
import { Icon } from '../components/icons/Icon';
import type { Skill, KennelItem } from '../lib/data/types';
import { loadSkills } from '../lib/data/domains/skills';

// ---- local hover-state helpers (not in store) ----------------
function SkillCard({ sk, operator }: { sk: Skill; operator: boolean }) {
  const removeSkill = useStore((s) => s.removeSkill);
  const [editHover, setEditHover] = useState(false);
  const [removeHover, setRemoveHover] = useState(false);

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
            onClick={() => {/* operator edit — noop per prototype */}}
            onMouseEnter={() => setEditHover(true)}
            onMouseLeave={() => setEditHover(false)}
            style={{
              border: 'none',
              background: editHover ? 'var(--hachi-cream)' : 'transparent',
              color: 'var(--hachi-muted)',
              cursor: 'pointer',
              display: 'inline-flex',
              padding: 4,
              borderRadius: 6,
              transition: 'background var(--dur-fast) var(--ease)',
            }}
            aria-label={`Edit ${sk.name}`}
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
            onClick={() => removeSkill(sk.id)}
            onMouseEnter={() => setRemoveHover(true)}
            onMouseLeave={() => setRemoveHover(false)}
            style={{
              border: 'none',
              background: 'transparent',
              color: removeHover ? 'var(--hachi-danger)' : 'var(--hachi-danger)',
              font: '500 12px var(--font-body)',
              cursor: 'pointer',
              padding: 0,
              textDecoration: removeHover ? 'underline' : 'none',
              transition: 'text-decoration var(--dur-fast) var(--ease)',
            }}
          >
            not used in 90 days — remove?
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

  useEffect(() => { void loadSkills() }, []);

  // "Browse the kennel" header button hover
  const [browseHover, setBrowseHover] = useState(false);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      {/* ---- top bar ---- */}
      <div
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
        {/* Browse the kennel — secondary-style button (navy outline, hover tint) */}
        <button
          onClick={() => {/* opens KennelConsent globally — noop; real flow uses addSkill */}}
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
        >
          <Icon name="skills" size={15} />
          Browse the kennel
        </button>
      </div>

      {/* ---- scrollable body ---- */}
      <div
        className="hachi-scroll"
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

          {/* ---- From the kennel section ---- */}
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
        </div>
      </div>
    </div>
  );
}

export const Route = createFileRoute('/skills')({
  component: Skills,
});
