# Per-Use-Case Model Selector — Design Spec

**Date:** 2026-05-21
**Status:** Approved

---

## Problem

The Settings page currently exposes a single "AI Provider" card that controls the **analysis service** only. The ML strategy codegen service (`services/ml/codegen.py`) has its provider and model hardcoded (`claude-sonnet-4-5`) with no way to change them from the UI. The user needs independent provider/model control for each use case.

---

## Approach

Two independent settings cards on the Settings page — one per use case. Each card has its own provider dropdown, conditional model dropdown, Save button, and API endpoint. They write to separate Redis keys and are read by their respective services at runtime.

---

## Settings UI

**Existing card** renamed from "AI Provider" to "Analysis Service". No functional change — provider, model dropdowns, and Save button behave identically to today.

**New card** "ML Strategy Codegen" added below, with identical structure:
- Provider dropdown: Claude (Anthropic) / Gemini (Google)
- Claude model row (shown when provider = Claude): same `CLAUDE_MODELS` list
- Gemini model row (shown when provider = Gemini): same `GEMINI_MODELS` list
- Hint text: "Used during the nightly ML pipeline run at 2am."
- Save button + status message

Each card's JS is self-contained (no shared state between cards). Save on one card does not affect the other.

---

## Backend — Dashboard

### New Redis keys

| Key | Purpose | Default |
|---|---|---|
| `config:ml_codegen_provider` | Active provider for ML codegen | `"claude"` |
| `config:ml_codegen_model` | Active model for ML codegen | `"claude-sonnet-4-5"` |

### New functions in `queries.py`

**`get_ml_codegen_settings() -> dict`**
Reads `config:ml_codegen_provider` and `config:ml_codegen_model` from Redis, falling back to `config.toml` `[ml]` defaults. Returns `{ codegen_provider, codegen_claude_model, codegen_gemini_model, claude_models, gemini_models }`.

**`set_ml_codegen_provider(provider: str, model: str) -> None`**
Validates provider and model against the existing `CLAUDE_MODELS` / `GEMINI_MODELS` lists, then writes to Redis. Raises `ValueError` on invalid input.

### Route changes in `main.py`

- `GET /settings`: passes both `get_ai_settings()` and `get_ml_codegen_settings()` to the template (merged dict or separate template vars — merged preferred to keep template simple)
- `POST /api/settings/ml-codegen`: new endpoint, same structure as `/api/settings/ai-provider`

### `config.toml` defaults (under `[ml]`)

```toml
codegen_provider = "claude"
codegen_model    = "claude-sonnet-4-5"
```

---

## ML Codegen — Gemini support (`codegen.py`)

### New function

```python
def _call_gemini(prompt: str, client) -> str:
    """Call Gemini API and return raw text response."""
```

Uses `google-generativeai` SDK (already a dependency of the analysis service; needs adding to `services/ml/requirements.txt`).

### Updated `generate_strategy_code()` signature

```python
def generate_strategy_code(
    pattern: CandidatePattern,
    client=None,
    provider: str = "claude",
    model: str = _MODEL,
) -> Optional[str]:
```

- `provider="claude"` + `model=_MODEL` preserves existing behaviour when called without the new args.
- Internally, the function selects `_call_claude` or `_call_gemini` based on `provider`. Validation and retry logic are unchanged — only the API call differs.
- The hardcoded `_MODEL = "claude-sonnet-4-5"` remains as the fallback default.

---

## ML Pipeline — reads settings at runtime (`pipeline.py`)

At the start of `_run_phases()`, before Phase 4, the pipeline reads the codegen provider and model from Redis (falling back to `config.toml` defaults). It instantiates the appropriate client and passes `provider` + `model` into `generate_strategy_code()`.

```python
# Phase 4: read codegen settings
codegen_provider = redis_get("config:ml_codegen_provider") or ml_cfg.get("codegen_provider", "claude")
codegen_model    = redis_get("config:ml_codegen_model")    or ml_cfg.get("codegen_model", "claude-sonnet-4-5")
```

Since the pipeline runs nightly, whatever is saved in the UI at 2am is what gets used — no restart required.

---

## Error Handling

- Invalid provider/model values rejected by `set_ml_codegen_provider()` with `ValueError`; the API endpoint returns `{ ok: false, error: "..." }` and HTTP 400.
- If Redis is unavailable at pipeline runtime, the fallback chain (`config.toml` → hardcoded default) ensures the pipeline still runs.
- Gemini API errors in codegen are handled the same as Claude errors: logged, one retry with error context appended to the prompt, then `None` returned (pipeline logs and skips that pattern).

---

## Testing

- `test_queries.py`: `get_ml_codegen_settings()` returns Redis value when set, falls back to config default when not set; `set_ml_codegen_provider()` rejects invalid provider/model.
- `test_codegen.py`: `generate_strategy_code()` calls `_call_gemini` when `provider="gemini"`; existing Claude tests unaffected.
- `test_pipeline.py`: `_run_phases()` reads codegen settings from Redis and passes them to `generate_strategy_code()`.
- No real API calls in tests — mock `_call_claude` and `_call_gemini`.

---

## Out of Scope

- Adding Gemini support to the analysis service (already exists).
- Model hot-swapping mid-pipeline-run.
- Per-symbol codegen model selection.
