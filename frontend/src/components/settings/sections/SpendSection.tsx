// SpendSection — per-task and daily spend caps + today's spend bar.
// Caps now persist live on commit (Enter / blur) via PUT /config/limits, so the
// old page-level "Save" button is gone — every Settings change saves on its own.

import { useState, useEffect, useRef } from 'react';
import { useStore } from '../../../lib/store/store';
import { Icon } from '../../icons/Icon';
import { setLimitsLive } from '../../../lib/data/domains/config';
import { cardStyle, sectionHeadingStyle, PanelHeader } from '../shared';

interface SpendCapInputProps {
  label: string;
  value: number;
  onCommit: (v: number) => void;
}

function SpendCapInput({ label, value, onCommit }: SpendCapInputProps) {
  const [editing, setEditing] = useState(false);
  const [localVal, setLocalVal] = useState(String(value.toFixed(2)));
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (editing) {
      setLocalVal(String(value.toFixed(2)));
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [editing, value]);

  function commit() {
    const parsed = parseFloat(localVal);
    if (!isNaN(parsed) && parsed > 0 && parsed !== value) {
      onCommit(parsed);
    }
    setEditing(false);
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') {
      e.preventDefault();
      commit();
    }
    if (e.key === 'Escape') {
      e.preventDefault();
      setEditing(false);
    }
  }

  return (
    <div style={{ flex: 1 }}>
      <div style={{ fontSize: 12, color: 'var(--hachi-muted)', marginBottom: 6 }}>{label}</div>
      {editing ? (
        <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <span style={{ fontFamily: 'var(--font-mono)', fontSize: 15, color: 'var(--hachi-muted)' }}>
            $
          </span>
          <input
            ref={inputRef}
            type="number"
            min="0.01"
            step="0.01"
            value={localVal}
            onChange={(e) => setLocalVal(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={commit}
            style={{
              flex: 1,
              height: 42,
              padding: '0 14px',
              borderRadius: 10,
              border: '0.5px solid var(--hachi-trust)',
              background: 'var(--hachi-canvas)',
              fontFamily: 'var(--font-mono)',
              fontSize: 15,
              color: 'var(--hachi-ink)',
              outline: 'none',
            }}
          />
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setEditing(true)}
          title="Click to edit"
          style={{
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            width: '100%',
            height: 42,
            padding: '0 14px',
            borderRadius: 10,
            border: '0.5px solid var(--hachi-line)',
            background: 'var(--hachi-canvas)',
            fontFamily: 'var(--font-mono)',
            fontSize: 15,
            color: 'var(--hachi-ink)',
            cursor: 'pointer',
            textAlign: 'left',
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--hachi-trust)';
          }}
          onMouseLeave={(e) => {
            (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--hachi-line)';
          }}
        >
          ${value.toFixed(2)}
          <span style={{ color: 'var(--hachi-muted)', fontSize: 12 }}>
            <Icon name="pencil" size={13} />
          </span>
        </button>
      )}
    </div>
  );
}

export default function SpendSection() {
  const spent = useStore((s) => s.spent);
  const budget = useStore((s) => s.budget);
  const taskCap = useStore((s) => s.taskCap);
  const dailyCap = useStore((s) => s.dailyCap);
  const setTaskCap = useStore((s) => s.setTaskCap);
  const setDailyCap = useStore((s) => s.setDailyCap);
  const showToast = useStore((s) => s.showToast);

  // Persist both caps to the gateway; report failure honestly. Reads the latest
  // caps from the store so we always send a consistent pair.
  async function persistLimits() {
    const s = useStore.getState();
    try {
      await setLimitsLive(s.taskCap, s.dailyCap);
      showToast('Spend limits saved', 'success');
    } catch {
      showToast('Could not save limits — please try again', 'blocked');
    }
  }

  function handleTaskCap(v: number) {
    setTaskCap(v);
    void persistLimits();
  }
  function handleDailyCap(v: number) {
    setDailyCap(v);
    void persistLimits();
  }

  const spentStr = '$' + Number(spent).toFixed(2);
  const pct = budget > 0 ? Math.min(100, Math.round((spent / budget) * 100)) : 0;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 24 }}>
      <PanelHeader title="Spend" subtitle="Caps and today's running cost." />

      <div style={cardStyle}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 16 }}>
          <span style={{ color: 'var(--hachi-ink)', display: 'inline-flex' }}>
            <Icon name="dollar" size={18} />
          </span>
          <h3 style={sectionHeadingStyle}>Spend &amp; limits</h3>
        </div>

        <div
          className="hachi-spend-cap-row"
          style={{ display: 'flex', gap: 14, marginBottom: 18 }}
        >
          <SpendCapInput label="Per-task cap" value={taskCap} onCommit={handleTaskCap} />
          <SpendCapInput label="Daily cap" value={dailyCap} onCommit={handleDailyCap} />
        </div>

        <div style={{ fontSize: 12, color: 'var(--hachi-muted)', marginBottom: 8 }}>
          Today, against ${dailyCap.toFixed(2)}
        </div>
        <div
          style={{
            height: 6,
            borderRadius: 999,
            background: 'var(--hachi-line)',
            overflow: 'hidden',
            marginBottom: 8,
          }}
        >
          <div
            style={{
              width: `${pct}%`,
              height: '100%',
              background: 'var(--hachi-brand)',
              borderRadius: 999,
              transition: 'width var(--dur) var(--ease)',
            }}
          />
        </div>
        <div style={{ fontSize: 13, color: 'var(--hachi-muted)' }}>
          What's costing money: model calls {spentStr} · tools $0.00
        </div>
      </div>
    </div>
  );
}
