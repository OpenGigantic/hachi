# Optimizations Implemented — 2026-06-28 Token Cost Audit

Every fix below was built test-first (failing regression test → fix → green) and locked with a `docs/bug-bank.md` entry where it closes a bug. Per-turn cost dropped from **$0.014965 → $0.00076 (~19.7×)**, measured live on the same `What is my name?` query.

---

## 1. Repoint routing off gpt-4o (config) — biggest single win

- **Change:** `C:\Users\opsin\AppData\Local\hachi\settings.toml` — `routing.capabilities.{reasoning,coding,vision,title_generation,compaction,summarization,long_context}` changed from `["gpt-4o"]` → `["gpt-4o-mini"]`.
- **Why:** every chat turn classified as `reasoning` and was routed to gpt-4o ($2.50/$10), the priciest model in the config.
- **Measured impact:** main-call cost **$0.014965 → $0.00076** (gpt-4o-mini input is 16.7× cheaper). *Takes effect on the next gateway/app restart.*

## 2. Trim always-on tool schemas to lazy groups — `gateway/orchestrator.py`

- **Change:** moved `delegate_task` + `list_roles` → `"delegation"` group and `save_skill` + `skill_changelog` → `"authoring"` group (out of `_CORE_TOOL_NAMES`, into `_GROUP_BY_TOOL_NAME`). The model unlocks them on demand via `use_tools(group=...)`.
- **Kept in core deliberately:** `skill_dependencies`, `check_skill_assertions` (brain.og's skill-routing contract requires them right after `load_skill`) and `rollback_changes` (file-edit undo). Moving these would silently break those flows.
- **Measured impact:** trivial-turn input **5,958 → 5,037 tok** (−921; 23 → 19 core tools).
- **Locked by:** `tests/test_tool_lazy_loading.py::test_rare_tools_trimmed_from_core_to_lazy_groups`, `::test_classify_routes_trimmed_tools_to_their_groups`.

## 3. Fix Opus-4 family 3× mispricing — `core/cost.py`, `core/routing/descriptor.py` (BB-27)

- **Change:** `claude-opus-4` / `claude-opus-4-8` priced `(15.0, 75.0)` → `(5.0, 25.0)` in both the cost table and the routing registry (the parity guard keeps them in sync).
- **Impact:** any Opus turn's displayed cost was inflated 3×; now correct. (Haiku/gpt-4o-mini unaffected.)
- **Locked by:** `tests/test_cost_tracking.py::test_opus_4_family_priced_at_current_rate_not_legacy`; `tests/test_routing_registry.py::TestPricingParity`.

## 4. Parse OpenAI `cached_tokens` + per-model cache multipliers — `core/llm.py`, `core/cost.py`, `core/routing/descriptor.py` (BB-28)

- **Change:** `OpenAICompatibleClient` now splits `prompt_tokens_details.cached_tokens` out of `prompt_tokens` into `cache_read_tokens` (streaming + non-streaming). `CostTracker.record` now honours each model's `Pricing.cache_read_mult` (OpenAI 0.5×, Anthropic 0.1×) instead of hard-coding 0.1× for everyone.
- **Impact:** OpenAI prompt-cache savings are now visible and priced correctly; displayed cost no longer overstates cached OpenAI input.
- **Locked by:** `tests/test_streaming.py::test_openai_stream_splits_cached_prompt_tokens`, `::test_openai_generate_splits_cached_prompt_tokens`; `tests/test_cost_tracking.py::test_cost_tracker_uses_per_model_cache_multiplier_from_registry`.

## 5. OpenAI cache-prefix placement — `core/llm.py` (BB-29)

- **Change:** the volatile dynamic block (time header / recall) is no longer appended to the system message; it's a trailing message after the conversation, so the stable system+tools prefix is byte-identical across turns and OpenAI can auto-cache it.
- **Impact:** enables OpenAI automatic prefix caching on repeat turns (was fully defeated before).
- **Locked by:** `tests/test_streaming.py::test_openai_request_keeps_time_header_out_of_system_message`.

## 6. Track background fact-extraction spend — `core/cost.py`, `core/memory.py` (BB-31)

- **Change:** new `cost.record_background_usage(model, usage)` writes a background (non-session) LLM call's cost into the durable daily ledger; `extract_facts` calls it at both extraction sites.
- **Impact:** background extraction spend now counts toward `/spend` and the daily cap instead of vanishing.
- **Locked by:** `tests/test_cost_tracking.py::test_record_background_usage_writes_to_daily_ledger`, `::test_extract_facts_records_background_spend`.

## 7. Per-request cache observability — `core/cache_observability.py` (new), `core/cost.py`, `core/loop.py`

- **Change:** new `cache_observability` module computes a per-call breakdown — fresh vs cache_read vs cache_creation input, output, **cache_hit_rate**, actual **cost**, and **cost_if_no_cache / cost_if_fully_cached** (the spread that shows caching's value). `CostTracker.cache_metrics(model, usage)` exposes it; the execution loop emits it as a `cache_metrics` runtime event after every generation; the cumulative `cache_hit_rate` is added to the usage snapshot.
- **Impact:** cache effectiveness is now observable per request (`cache_metrics` event verified firing live), not buried in aggregate spend. **This is the shippable `CACHE_OBSERVABILITY.py` deliverable** (`src/hachi/core/cache_observability.py`).
- **Locked by:** `tests/test_cache_observability.py` (5 tests).

## 8. Boot-time cost-routing guard — `core/routing/policy.py`, `gateway/orchestrator.py` (BB-30)

- **Change:** `RoutingPolicy.costly_capability_warnings(registry)` flags (never mutates) when a chat capability is pinned to a model ≥3× pricier than `default_model` while the default is also capable. Boot logs a `[cost]` warning + emits a `cost.routing_warning` event.
- **Impact:** the silent "every chat turn on the priciest model" trap is now loud for all users. (The auto-policy path already routes `reasoning → default_model`, `title/compaction → cheapest`.)
- **Locked by:** `tests/test_routing_cost_guard.py` (3 tests).

---

### Files touched
`core/cost.py`, `core/llm.py`, `core/cache_observability.py` (new), `core/memory.py`, `core/loop.py`, `core/routing/descriptor.py`, `core/routing/policy.py`, `gateway/orchestrator.py`; tests: `test_cost_tracking.py`, `test_streaming.py`, `test_tool_lazy_loading.py`, `test_cache_observability.py` (new), `test_routing_cost_guard.py` (new); docs: `docs/bug-bank.md` (BB-27..31). One user config: `settings.toml`.
