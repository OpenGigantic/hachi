# ObjectGraph (.og) Token-Efficiency Benchmark

**TL;DR:** Answering a query by routing through the 3-pass ObjectGraph model
uses **97.2% fewer tokens** than naively concatenating all skill files into the
prompt — measured against the 42-node bundled skill pack shipped with Tokoboros.

Reproducible at any time:

```bash
uv run tokoboros skill benchmark           # human-readable panel
uv run tokoboros skill benchmark --json    # machine-readable JSON (for CI / sharing)
uv run pytest tests/test_og_benchmark.py -s  # unit-test form with raw numbers
```

---

## Why this matters

Every modern agent framework faces a **context-loading problem**: skill files,
tool docs, and examples must reach the model somehow.  The naive solution —
concatenate everything — is simple but catastrophically wasteful as the skill
library grows.  Worse, irrelevant content adds noise that degrades answer
quality and inflates per-turn costs.

The [ObjectGraph format](https://arxiv.org/abs/2604.27820) (arXiv 2604.27820)
treats the context-loading problem as a **file-format problem**.  Rather than
a flat document you load or skip, a `.og` file is a **queryable graph of typed
nodes**, each carrying structured metadata that agents can index and traverse
without reading full content.

Independent research confirms the premise:

| Paper | Finding |
|-------|---------|
| Graph-of-Skills (arXiv 2604.05333) | −56.72% tokens / +25.55% peak reward (GPT-5.2 Codex) vs flat skill loading |
| SkillReducer (arXiv 2603.29919) | ~60% of skill body is non-actionable; 39–48% compression with +2.8% quality |

Neither paper used `.og` — they validate that **format-aware selective loading**
is the right category of solution.  `.og` is Tokoboros's concrete implementation
of that insight, with sub-file node granularity and dependency-aware traversal
that file-level lazy loading cannot provide.

---

## The 3-pass model

When the agent needs a skill it runs three ranked passes, stopping as soon as it
has what it needs:

| Pass | Content loaded | Cost |
|------|---------------|------|
| **Pass 1 — index** | `::index` routing table (node names + role + keywords) | ~30 tok / file |
| **Pass 2 — dense** | `::dense` compressed summaries for the matching candidates | ~150–300 tok total |
| **Pass 3 — full** | `::full` content for the single best node | exact skill size |

The majority of queries complete at Pass 2.  Pass 3 is only reached when the
agent needs to execute, not just route.

Compare this to **competitor approaches**:

| Approach | Granularity | Dependency-aware? |
|----------|------------|-------------------|
| Dump all `.md` into system prompt | File (all-or-nothing) | No |
| Anthropic Agent Skills (3-level) | File | No |
| MCP tool filtering | Tool schema | No |
| **ObjectGraph 3-pass** | **Sub-file node** | **Yes** (`requires` edges) |

---

## Current results (bundled default_skills)

Measured against the 42 `.og` nodes shipped with Tokoboros:

| Metric | Value |
|--------|-------|
| Skill nodes | 42 |
| Baseline — dump all (tokens) | 9,016 |
| 3-pass total (tokens) | 253 |
| &nbsp;&nbsp;└ Pass 1 — index search | 39 |
| &nbsp;&nbsp;└ Pass 2 — dense peek (top 5) | 86 |
| &nbsp;&nbsp;└ Pass 3 — full resolve (1 node) | 128 |
| **Token savings** | **97.2%** |
| CI gate | ≥ 80% |

The CI gate enforces that this savings ratio never regresses below 80% as the
skill library grows.

---

## Methodology

### Token estimator

`estimate_tokens(text)` is a lightweight approximation (chars / 4, roughly
matching GPT-3.5-style BPE for English prose and JSON).  It is intentionally
conservative — actual savings with the Anthropic tokenizer are typically higher
because the index JSON structure is tokenized more efficiently than prose.

### Baseline

The baseline simulates the "dump-all" pattern: every node's `::full` content is
concatenated with `\n` separators and the total estimated token count is used
as the denominator.

### 3-pass measurement

1. Run `engine.search(query)` → top-5 candidate index entries → `estimate_tokens(json.dumps(results[:5]))`.
2. Run `engine.peek(candidates)` → dense summaries for all 5 → `estimate_tokens(json.dumps(...))`.
3. Run `engine.resolve_node(candidates[0])` → full content for the single best node → `estimate_tokens(...)`.

The representative query is the first keyword from the engine's index (a
repeatable, version-controlled selection that always resolves to at least one
match).

### CI enforcement

The benchmark runs as both a pytest test (`tests/test_og_benchmark.py`,
threshold 80%) and a dedicated CI job (`benchmark` in `.github/workflows/ci.yml`)
that runs `skill benchmark --json --threshold 0.80` and fails the build if
savings drop below the gate.

---

## Running against your own skill pack

```bash
uv run tokoboros skill benchmark --dir /path/to/your/skills
uv run tokoboros skill benchmark --dir /path/to/your/skills --json
```

The `--threshold` flag lets you set a custom gate:

```bash
uv run tokoboros skill benchmark --threshold 0.90  # fail unless savings ≥ 90%
```

---

## Sharing results

Run `skill benchmark --json` and paste the output as a snippet in your README or
submit it to the Tokoboros skill registry.  Example output:

```json
{
  "nodes": 42,
  "baseline_tokens": 9016,
  "three_pass_tokens": 253,
  "savings_pct": 0.972,
  "p1_tokens": 39,
  "p2_tokens": 86,
  "p3_tokens": 128,
  "threshold": 0.8,
  "passed": true,
  "query": "schedule"
}
```
