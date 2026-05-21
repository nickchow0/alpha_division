# Per-Use-Case Model Selector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add independent provider/model selection for the ML strategy codegen service alongside the existing analysis service selector in the Settings page.

**Architecture:** Two new Redis keys store the codegen provider and model. The dashboard exposes a new API endpoint and a second settings card in the UI. The ML codegen module gains a Gemini code path. The pipeline reads the Redis keys at runtime before Phase 4.

**Tech Stack:** Python, Flask, Jinja2, Redis, Anthropic SDK, google-generativeai==0.8.3, pytest

---

## File Map

| File | Change |
|---|---|
| `config.toml` | Add `codegen_provider` and `codegen_model` under `[ml]` |
| `services/ml/requirements.txt` | Add `google-generativeai==0.8.3` |
| `services/dashboard/queries.py` | Add `get_ml_codegen_settings()`, `set_ml_codegen_provider()`, 3 new Redis key constants |
| `services/dashboard/main.py` | Update `/settings` route; add `POST /api/settings/ml-codegen` endpoint |
| `services/dashboard/templates/settings.html` | Rename existing card to "Analysis Service"; add "ML Strategy Codegen" card |
| `services/dashboard/tests/test_queries.py` | Add `TestGetMlCodegenSettings` and `TestSetMlCodegenProvider` test classes |
| `services/ml/codegen.py` | Add `_call_gemini()`; update `generate_strategy_code()` to accept `provider`, `model`, `gemini_api_key` |
| `services/ml/pipeline.py` | Import `get_redis`; read codegen settings from Redis before Phase 4; conditionally build client |
| `services/ml/tests/test_codegen.py` | Add Gemini tests; verify existing Claude tests still pass with new signature |

---

## Task 1: Config defaults and ML requirements

**Files:**
- Modify: `config.toml`
- Modify: `services/ml/requirements.txt`

- [ ] **Step 1: Add codegen defaults to config.toml**

In `config.toml`, append two lines inside the `[ml]` block (after `cron_schedule`):

```toml
codegen_provider = "claude"
codegen_model    = "claude-sonnet-4-5"
```

- [ ] **Step 2: Add google-generativeai to ML requirements**

In `services/ml/requirements.txt`, add after the `anthropic` line:

```
google-generativeai==0.8.3
```

- [ ] **Step 3: Rebuild ML container**

```bash
docker-compose -f docker-compose.yml up -d --build ml
```

Expected: container restarts cleanly. Verify with:
```bash
docker-compose -f docker-compose.yml exec -T ml python -c "import google.generativeai; print('ok')"
```
Expected output: `ok`

- [ ] **Step 4: Commit**

```bash
git add config.toml services/ml/requirements.txt
git commit -m "chore: add codegen config defaults and google-generativeai dependency"
```

---

## Task 2: Dashboard queries — ML codegen settings

**Files:**
- Modify: `services/dashboard/queries.py`
- Test: `services/dashboard/tests/test_queries.py`

- [ ] **Step 1: Write failing tests**

Add at the bottom of `services/dashboard/tests/test_queries.py`, after the existing imports add `get_ml_codegen_settings` and `set_ml_codegen_provider` to the import block:

```python
from queries import (
    # ... existing imports ...
    get_ml_codegen_settings,
    set_ml_codegen_provider,
)
```

Then add these two test classes at the end of the file:

```python
class TestGetMlCodegenSettings(unittest.TestCase):
    @patch("queries.get_redis")
    @patch("queries.load_config")
    def test_returns_config_defaults_when_redis_empty(self, mock_load_config, mock_get_redis):
        mock_load_config.return_value = {
            "ml": {"codegen_provider": "claude", "codegen_model": "claude-sonnet-4-5"}
        }
        mock_redis = MagicMock()
        mock_redis.get.return_value = None
        mock_get_redis.return_value = mock_redis

        result = get_ml_codegen_settings()

        self.assertEqual(result["codegen_provider"], "claude")
        self.assertEqual(result["codegen_claude_model"], "claude-sonnet-4-5")
        self.assertIn("claude_models", result)
        self.assertIn("gemini_models", result)

    @patch("queries.get_redis")
    @patch("queries.load_config")
    def test_redis_values_override_config(self, mock_load_config, mock_get_redis):
        mock_load_config.return_value = {"ml": {}}
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda key: {
            "config:ml_codegen_provider": b"gemini",
            "config:ml_codegen_claude_model": None,
            "config:ml_codegen_gemini_model": b"gemini-1.5-pro",
        }.get(key)
        mock_get_redis.return_value = mock_redis

        result = get_ml_codegen_settings()

        self.assertEqual(result["codegen_provider"], "gemini")
        self.assertEqual(result["codegen_gemini_model"], "gemini-1.5-pro")

    @patch("queries.get_redis")
    @patch("queries.load_config")
    def test_decodes_bytes_from_redis(self, mock_load_config, mock_get_redis):
        mock_load_config.return_value = {"ml": {}}
        mock_redis = MagicMock()
        mock_redis.get.side_effect = lambda key: {
            "config:ml_codegen_provider": b"claude",
            "config:ml_codegen_claude_model": b"claude-sonnet-4-5",
            "config:ml_codegen_gemini_model": None,
        }.get(key)
        mock_get_redis.return_value = mock_redis

        result = get_ml_codegen_settings()

        self.assertIsInstance(result["codegen_provider"], str)
        self.assertIsInstance(result["codegen_claude_model"], str)


class TestSetMlCodegenProvider(unittest.TestCase):
    @patch("queries.get_redis")
    def test_sets_claude_provider_and_model(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        set_ml_codegen_provider("claude", "claude-sonnet-4-5")

        mock_redis.set.assert_any_call("config:ml_codegen_provider", "claude")
        mock_redis.set.assert_any_call("config:ml_codegen_claude_model", "claude-sonnet-4-5")

    @patch("queries.get_redis")
    def test_sets_gemini_provider_and_model(self, mock_get_redis):
        mock_redis = MagicMock()
        mock_get_redis.return_value = mock_redis

        set_ml_codegen_provider("gemini", "gemini-2.0-flash")

        mock_redis.set.assert_any_call("config:ml_codegen_provider", "gemini")
        mock_redis.set.assert_any_call("config:ml_codegen_gemini_model", "gemini-2.0-flash")

    @patch("queries.get_redis")
    def test_raises_on_unknown_provider(self, mock_get_redis):
        with self.assertRaises(ValueError):
            set_ml_codegen_provider("openai", "gpt-4")

    @patch("queries.get_redis")
    def test_raises_on_unknown_claude_model(self, mock_get_redis):
        with self.assertRaises(ValueError):
            set_ml_codegen_provider("claude", "claude-opus-99")

    @patch("queries.get_redis")
    def test_raises_on_unknown_gemini_model(self, mock_get_redis):
        with self.assertRaises(ValueError):
            set_ml_codegen_provider("gemini", "gemini-ultra-99")
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
docker-compose -f docker-compose.yml exec -T dashboard python -m pytest tests/test_queries.py::TestGetMlCodegenSettings tests/test_queries.py::TestSetMlCodegenProvider -x -q 2>&1 | tail -10
```

Expected: ImportError or AttributeError (functions don't exist yet).

- [ ] **Step 3: Implement the functions in queries.py**

Add after the existing `_GEMINI_MODEL_KEY` constant block (around line 606):

```python
_ML_CODEGEN_PROVIDER_KEY     = "config:ml_codegen_provider"
_ML_CODEGEN_CLAUDE_MODEL_KEY = "config:ml_codegen_claude_model"
_ML_CODEGEN_GEMINI_MODEL_KEY = "config:ml_codegen_gemini_model"


def get_ml_codegen_settings() -> dict:
    """
    Return the current ML codegen provider settings.

    Reads from Redis, falling back to config.toml [ml] defaults.
    """
    cfg = load_config()
    ml_cfg = cfg.get("ml", {})
    r = get_redis()

    provider     = r.get(_ML_CODEGEN_PROVIDER_KEY)     or ml_cfg.get("codegen_provider", "claude")
    claude_model = r.get(_ML_CODEGEN_CLAUDE_MODEL_KEY) or ml_cfg.get("codegen_model", "claude-sonnet-4-5")
    gemini_model = r.get(_ML_CODEGEN_GEMINI_MODEL_KEY) or "gemini-2.0-flash"

    if isinstance(provider, bytes):
        provider = provider.decode()
    if isinstance(claude_model, bytes):
        claude_model = claude_model.decode()
    if isinstance(gemini_model, bytes):
        gemini_model = gemini_model.decode()

    return {
        "codegen_provider":     provider,
        "codegen_claude_model": claude_model,
        "codegen_gemini_model": gemini_model,
        "claude_models":        CLAUDE_MODELS,
        "gemini_models":        GEMINI_MODELS,
    }


def set_ml_codegen_provider(provider: str, model: str) -> None:
    """
    Persist ML codegen provider and model to Redis.

    Raises ValueError for unknown provider or model values.
    """
    if provider not in ("claude", "gemini"):
        raise ValueError(f"Unknown provider '{provider}' — must be 'claude' or 'gemini'")
    if provider == "claude" and model not in CLAUDE_MODELS:
        raise ValueError(f"Unknown Claude model '{model}'")
    if provider == "gemini" and model not in GEMINI_MODELS:
        raise ValueError(f"Unknown Gemini model '{model}'")

    r = get_redis()
    r.set(_ML_CODEGEN_PROVIDER_KEY, provider)
    if provider == "claude":
        r.set(_ML_CODEGEN_CLAUDE_MODEL_KEY, model)
    else:
        r.set(_ML_CODEGEN_GEMINI_MODEL_KEY, model)
```

- [ ] **Step 4: Run tests to confirm they pass**

```bash
docker-compose -f docker-compose.yml exec -T dashboard python -m pytest tests/test_queries.py::TestGetMlCodegenSettings tests/test_queries.py::TestSetMlCodegenProvider -x -q 2>&1 | tail -10
```

Expected: `10 passed`

- [ ] **Step 5: Run full dashboard test suite to check for regressions**

```bash
docker-compose -f docker-compose.yml exec -T dashboard python -m pytest tests/test_queries.py -q 2>&1 | tail -5
```

Expected: all existing tests still pass.

- [ ] **Step 6: Commit**

```bash
git add services/dashboard/queries.py services/dashboard/tests/test_queries.py
git commit -m "feat: add get_ml_codegen_settings and set_ml_codegen_provider to dashboard queries"
```

---

## Task 3: Dashboard route and API endpoint

**Files:**
- Modify: `services/dashboard/main.py`

- [ ] **Step 1: Update the imports in main.py**

Find the import block near line 32 that imports `get_ai_settings`:

```python
from queries import (
    ...
    get_ai_settings,
    set_ai_provider,
    ...
)
```

Add `get_ml_codegen_settings` and `set_ml_codegen_provider` to the same import block:

```python
from queries import (
    ...
    get_ai_settings,
    set_ai_provider,
    get_ml_codegen_settings,
    set_ml_codegen_provider,
    ...
)
```

- [ ] **Step 2: Update the /settings route to pass codegen settings**

Find the existing `/settings` route (around line 256):

```python
@app.route("/settings")
def settings():
    return render_template("settings.html", **get_ai_settings())
```

Replace with:

```python
@app.route("/settings")
def settings():
    return render_template("settings.html", **get_ai_settings(), **get_ml_codegen_settings())
```

- [ ] **Step 3: Add the new API endpoint**

Find the existing `/api/settings/ai-provider` endpoint. Add the new endpoint directly after it:

```python
@app.route("/api/settings/ml-codegen", methods=["POST"])
def api_settings_ml_codegen():
    data     = request.get_json() or {}
    provider = data.get("provider", "")
    model    = data.get("model", "")
    try:
        set_ml_codegen_provider(provider, model)
        return jsonify(ok=True, provider=provider, model=model)
    except ValueError as exc:
        return jsonify(ok=False, error=str(exc)), 400
```

- [ ] **Step 4: Rebuild dashboard and smoke-test the endpoint**

```bash
docker-compose -f docker-compose.yml up -d --build dashboard
```

```bash
curl -s -X POST http://localhost:8080/api/settings/ml-codegen \
  -H 'Content-Type: application/json' \
  -d '{"provider":"claude","model":"claude-sonnet-4-5"}' | python3 -c "import sys,json; print(json.load(sys.stdin))"
```

Expected: `{'ok': True, 'provider': 'claude', 'model': 'claude-sonnet-4-5'}`

```bash
curl -s -X POST http://localhost:8080/api/settings/ml-codegen \
  -H 'Content-Type: application/json' \
  -d '{"provider":"bad","model":"x"}' | python3 -c "import sys,json; print(json.load(sys.stdin))"
```

Expected: `{'ok': False, 'error': "Unknown provider 'bad' ..."}`

- [ ] **Step 5: Commit**

```bash
git add services/dashboard/main.py
git commit -m "feat: add /api/settings/ml-codegen endpoint and pass codegen settings to template"
```

---

## Task 4: Settings UI — rename card and add ML codegen card

**Files:**
- Modify: `services/dashboard/templates/settings.html`

- [ ] **Step 1: Replace the full settings.html content**

Replace the entire content of `services/dashboard/templates/settings.html` with:

```html
{% extends "base.html" %}
{% block title %} — Settings{% endblock %}

{% block content %}
<h1>Settings</h1>

<div class="section">
  <h2>Analysis Service</h2>
  <div class="settings-card">

    <div class="settings-row">
      <label class="settings-label" for="provider-select">Provider</label>
      <div class="settings-control">
        <select id="provider-select" class="settings-select">
          <option value="claude" {% if provider == 'claude' %}selected{% endif %}>Claude (Anthropic)</option>
          <option value="gemini" {% if provider == 'gemini' %}selected{% endif %}>Gemini (Google)</option>
        </select>
      </div>
    </div>

    <div class="settings-row" id="claude-model-row" {% if provider != 'claude' %}style="display:none"{% endif %}>
      <label class="settings-label" for="claude-model-select">Claude Model</label>
      <div class="settings-control">
        <select id="claude-model-select" class="settings-select">
          {% for m in claude_models %}
          <option value="{{ m }}" {% if m == claude_model %}selected{% endif %}>{{ m }}</option>
          {% endfor %}
        </select>
        <p class="settings-hint">Haiku is faster and cheaper; Sonnet reasons more deeply.</p>
      </div>
    </div>

    <div class="settings-row" id="gemini-model-row" {% if provider != 'gemini' %}style="display:none"{% endif %}>
      <label class="settings-label" for="gemini-model-select">Gemini Model</label>
      <div class="settings-control">
        <select id="gemini-model-select" class="settings-select">
          {% for m in gemini_models %}
          <option value="{{ m }}" {% if m == gemini_model %}selected{% endif %}>{{ m }}</option>
          {% endfor %}
        </select>
        <p class="settings-hint">Flash is faster and cheaper; Pro reasons more deeply.</p>
      </div>
    </div>

    <div class="settings-row">
      <div class="settings-label"></div>
      <div class="settings-control">
        <button id="analysis-save-btn" class="save-btn">Save</button>
        <span id="analysis-save-status" class="save-status"></span>
      </div>
    </div>

  </div>
</div>

<div class="section">
  <h2>ML Strategy Codegen</h2>
  <div class="settings-card">

    <div class="settings-row">
      <label class="settings-label" for="codegen-provider-select">Provider</label>
      <div class="settings-control">
        <select id="codegen-provider-select" class="settings-select">
          <option value="claude" {% if codegen_provider == 'claude' %}selected{% endif %}>Claude (Anthropic)</option>
          <option value="gemini" {% if codegen_provider == 'gemini' %}selected{% endif %}>Gemini (Google)</option>
        </select>
      </div>
    </div>

    <div class="settings-row" id="codegen-claude-model-row" {% if codegen_provider != 'claude' %}style="display:none"{% endif %}>
      <label class="settings-label" for="codegen-claude-model-select">Claude Model</label>
      <div class="settings-control">
        <select id="codegen-claude-model-select" class="settings-select">
          {% for m in claude_models %}
          <option value="{{ m }}" {% if m == codegen_claude_model %}selected{% endif %}>{{ m }}</option>
          {% endfor %}
        </select>
        <p class="settings-hint">Used during the nightly ML pipeline run at 2am.</p>
      </div>
    </div>

    <div class="settings-row" id="codegen-gemini-model-row" {% if codegen_provider != 'gemini' %}style="display:none"{% endif %}>
      <label class="settings-label" for="codegen-gemini-model-select">Gemini Model</label>
      <div class="settings-control">
        <select id="codegen-gemini-model-select" class="settings-select">
          {% for m in gemini_models %}
          <option value="{{ m }}" {% if m == codegen_gemini_model %}selected{% endif %}>{{ m }}</option>
          {% endfor %}
        </select>
        <p class="settings-hint">Used during the nightly ML pipeline run at 2am.</p>
      </div>
    </div>

    <div class="settings-row">
      <div class="settings-label"></div>
      <div class="settings-control">
        <button id="codegen-save-btn" class="save-btn">Save</button>
        <span id="codegen-save-status" class="save-status"></span>
      </div>
    </div>

  </div>
</div>
{% endblock %}

{% block extra_js %}
<script>
  // ── Analysis Service card ────────────────────────────────────────────────────
  const providerSelect     = document.getElementById('provider-select');
  const claudeModelRow     = document.getElementById('claude-model-row');
  const geminiModelRow     = document.getElementById('gemini-model-row');
  const claudeModelSelect  = document.getElementById('claude-model-select');
  const geminiModelSelect  = document.getElementById('gemini-model-select');
  const analysisSaveBtn    = document.getElementById('analysis-save-btn');
  const analysisSaveStatus = document.getElementById('analysis-save-status');

  function updateAnalysisModelVisibility() {
    const isGemini = providerSelect.value === 'gemini';
    claudeModelRow.style.display = isGemini ? 'none' : '';
    geminiModelRow.style.display = isGemini ? '' : 'none';
  }

  providerSelect.addEventListener('change', updateAnalysisModelVisibility);

  analysisSaveBtn.addEventListener('click', async () => {
    const provider = providerSelect.value;
    const model    = provider === 'gemini'
      ? geminiModelSelect.value
      : claudeModelSelect.value;

    analysisSaveBtn.disabled = true;
    analysisSaveStatus.textContent = 'Saving…';
    analysisSaveStatus.className = 'save-status';

    try {
      const resp = await fetch('/api/settings/ai-provider', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, model }),
      });
      const data = await resp.json();
      if (data.ok) {
        analysisSaveStatus.textContent = `Saved — analysis service will use ${data.provider} / ${data.model} within seconds.`;
        analysisSaveStatus.className = 'save-status positive';
      } else {
        analysisSaveStatus.textContent = `Error: ${data.error}`;
        analysisSaveStatus.className = 'save-status negative';
      }
    } catch (err) {
      analysisSaveStatus.textContent = 'Network error — try again.';
      analysisSaveStatus.className = 'save-status negative';
    } finally {
      analysisSaveBtn.disabled = false;
    }
  });

  // ── ML Strategy Codegen card ─────────────────────────────────────────────────
  const codegenProviderSelect    = document.getElementById('codegen-provider-select');
  const codegenClaudeModelRow    = document.getElementById('codegen-claude-model-row');
  const codegenGeminiModelRow    = document.getElementById('codegen-gemini-model-row');
  const codegenClaudeModelSelect = document.getElementById('codegen-claude-model-select');
  const codegenGeminiModelSelect = document.getElementById('codegen-gemini-model-select');
  const codegenSaveBtn           = document.getElementById('codegen-save-btn');
  const codegenSaveStatus        = document.getElementById('codegen-save-status');

  function updateCodegenModelVisibility() {
    const isGemini = codegenProviderSelect.value === 'gemini';
    codegenClaudeModelRow.style.display = isGemini ? 'none' : '';
    codegenGeminiModelRow.style.display = isGemini ? '' : 'none';
  }

  codegenProviderSelect.addEventListener('change', updateCodegenModelVisibility);

  codegenSaveBtn.addEventListener('click', async () => {
    const provider = codegenProviderSelect.value;
    const model    = provider === 'gemini'
      ? codegenGeminiModelSelect.value
      : codegenClaudeModelSelect.value;

    codegenSaveBtn.disabled = true;
    codegenSaveStatus.textContent = 'Saving…';
    codegenSaveStatus.className = 'save-status';

    try {
      const resp = await fetch('/api/settings/ml-codegen', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider, model }),
      });
      const data = await resp.json();
      if (data.ok) {
        codegenSaveStatus.textContent = `Saved — ML codegen will use ${data.provider} / ${data.model} on the next pipeline run.`;
        codegenSaveStatus.className = 'save-status positive';
      } else {
        codegenSaveStatus.textContent = `Error: ${data.error}`;
        codegenSaveStatus.className = 'save-status negative';
      }
    } catch (err) {
      codegenSaveStatus.textContent = 'Network error — try again.';
      codegenSaveStatus.className = 'save-status negative';
    } finally {
      codegenSaveBtn.disabled = false;
    }
  });
</script>
{% endblock %}
```

- [ ] **Step 2: Rebuild dashboard and verify in browser**

```bash
docker-compose -f docker-compose.yml up -d --build dashboard
```

Open http://localhost:8080/settings and verify:
- The existing card is now labelled "Analysis Service" (not "AI Provider")
- A second "ML Strategy Codegen" card appears below it
- Switching provider in each card independently shows/hides the correct model row
- Save on the Analysis card still works (status message appears)
- Save on the ML Codegen card posts to `/api/settings/ml-codegen` (status message appears)

- [ ] **Step 3: Commit**

```bash
git add services/dashboard/templates/settings.html
git commit -m "feat: add ML strategy codegen settings card to Settings page"
```

---

## Task 5: ML codegen — Gemini support

**Files:**
- Modify: `services/ml/codegen.py`
- Test: `services/ml/tests/test_codegen.py`

- [ ] **Step 1: Write failing tests**

Add to `services/ml/tests/test_codegen.py` after the existing imports:

```python
from codegen import generate_strategy_code, _validate_code, _build_prompt, _call_gemini
```

Add a helper and new test functions at the bottom of the file:

```python
def _mock_gemini_response(code: str):
    """Return a mock Gemini API response containing the given code."""
    resp = MagicMock()
    resp.text = f"```python\n{code}\n```"
    return resp


def test_call_gemini_returns_response_text():
    with patch("codegen.genai.configure") as mock_cfg, \
         patch("codegen.genai.GenerativeModel") as MockModel:
        mock_instance = MagicMock()
        mock_instance.generate_content.return_value = _mock_gemini_response(_VALID_CODE)
        MockModel.return_value = mock_instance

        result = _call_gemini("test prompt", api_key="fake-key", model="gemini-2.0-flash")

        mock_cfg.assert_called_once_with(api_key="fake-key")
        MockModel.assert_called_once_with("gemini-2.0-flash")
        assert "generate_signal" in result


def test_generate_strategy_code_gemini_success():
    pattern = _make_pattern()
    with patch("codegen.genai.configure"), \
         patch("codegen.genai.GenerativeModel") as MockModel:
        mock_instance = MagicMock()
        mock_instance.generate_content.return_value = _mock_gemini_response(_VALID_CODE)
        MockModel.return_value = mock_instance

        result = generate_strategy_code(
            pattern, provider="gemini", model="gemini-2.0-flash", gemini_api_key="fake-key"
        )

    assert result is not None
    assert "generate_signal" in result


def test_generate_strategy_code_gemini_retries_once_on_invalid():
    pattern = _make_pattern()
    with patch("codegen.genai.configure"), \
         patch("codegen.genai.GenerativeModel") as MockModel:
        mock_instance = MagicMock()
        mock_instance.generate_content.side_effect = [
            _mock_gemini_response(_INVALID_CODE_NO_FUNCTION),
            _mock_gemini_response(_VALID_CODE),
        ]
        MockModel.return_value = mock_instance

        result = generate_strategy_code(
            pattern, provider="gemini", model="gemini-2.0-flash", gemini_api_key="fake-key"
        )

    assert result is not None
    assert mock_instance.generate_content.call_count == 2


def test_generate_strategy_code_claude_unchanged_with_new_params():
    """Existing Claude path still works when provider/model are explicitly passed."""
    pattern = _make_pattern()
    mock_client = MagicMock()
    mock_client.messages.create.return_value = _mock_anthropic_response(_VALID_CODE)

    result = generate_strategy_code(
        pattern, client=mock_client, provider="claude", model="claude-sonnet-4-5"
    )

    assert result is not None
    mock_client.messages.create.assert_called_once()
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
docker-compose -f docker-compose.yml exec -T ml python -m pytest tests/test_codegen.py::test_call_gemini_returns_response_text tests/test_codegen.py::test_generate_strategy_code_gemini_success -x -q 2>&1 | tail -10
```

Expected: ImportError or AttributeError (`_call_gemini` doesn't exist yet).

- [ ] **Step 3: Implement Gemini support in codegen.py**

At the top of `services/ml/codegen.py`, add the import after `import anthropic`:

```python
import google.generativeai as genai
```

Add `_GEMINI_TEMPERATURE = 0.2` after `_MAX_TOKENS = 1024`.

Add `_call_gemini` function after the existing `_call_claude` function:

```python
def _call_gemini(prompt: str, api_key: str, model: str) -> str:
    """Call Gemini API and return the raw text response."""
    genai.configure(api_key=api_key)
    gemini_model = genai.GenerativeModel(model)
    response = gemini_model.generate_content(
        prompt,
        generation_config=genai.types.GenerationConfig(
            max_output_tokens=_MAX_TOKENS,
            temperature=_GEMINI_TEMPERATURE,
        ),
    )
    return response.text
```

Replace the `generate_strategy_code` function signature and body:

```python
def generate_strategy_code(
    pattern: CandidatePattern,
    client=None,
    provider: str = "claude",
    model: str = _MODEL,
    gemini_api_key: Optional[str] = None,
) -> Optional[str]:
    """Generate and validate a generate_signal() function for the given pattern.

    Returns the validated code string, or None if both attempts fail.
    The caller is responsible for saving the code to the database.
    """
    if provider == "claude":
        if client is None:
            client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    elif provider == "gemini":
        if gemini_api_key is None:
            gemini_api_key = os.environ["GEMINI_API_KEY"]

    prompt = _build_prompt(pattern)

    for attempt in range(2):
        log.info("Codegen attempt %d for pattern: %.60s...", attempt + 1, pattern.rule_description)
        try:
            if provider == "gemini":
                raw_text = _call_gemini(prompt, gemini_api_key, model)
            else:
                raw_text = _call_claude(prompt, client)
        except Exception as exc:  # noqa: BLE001
            log.error("API call failed (attempt %d): %s", attempt + 1, exc)
            return None

        code = _extract_code_block(raw_text)
        errors = _validate_code(code)

        if not errors:
            log.info("Codegen succeeded on attempt %d", attempt + 1)
            return code

        log.warning("Codegen attempt %d invalid: %s", attempt + 1, "; ".join(errors))
        if attempt == 0:
            prompt += f"\n\nYour previous response had these errors:\n" + "\n".join(
                f"- {e}" for e in errors
            ) + "\n\nPlease fix them and try again."

    log.error("Codegen failed after 2 attempts for pattern: %.60s...", pattern.rule_description)
    return None
```

- [ ] **Step 4: Run all codegen tests**

```bash
docker-compose -f docker-compose.yml exec -T ml python -m pytest tests/test_codegen.py -q 2>&1 | tail -10
```

Expected: all tests pass (new Gemini tests + all existing Claude tests).

- [ ] **Step 5: Commit**

```bash
git add services/ml/codegen.py services/ml/tests/test_codegen.py
git commit -m "feat: add Gemini support to ML strategy codegen"
```

---

## Task 6: ML pipeline — reads codegen settings at runtime

**Files:**
- Modify: `services/ml/pipeline.py`

- [ ] **Step 1: Write a failing test**

Create `services/ml/tests/test_pipeline_codegen.py`:

```python
"""Tests for pipeline codegen settings reading in _run_phases."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../../"))

from unittest.mock import patch, MagicMock, call
import pytest


def _make_mock_redis(provider=b"claude", claude_model=b"claude-sonnet-4-5", gemini_model=None):
    r = MagicMock()
    def _get(key):
        return {
            "config:ml_codegen_provider":     provider,
            "config:ml_codegen_claude_model": claude_model,
            "config:ml_codegen_gemini_model": gemini_model,
        }.get(key)
    r.get.side_effect = _get
    return r


@patch("pipeline.save_ml_run")
@patch("pipeline.save_ml_strategy")
@patch("pipeline.ensure_ml_tables")
@patch("pipeline._send_discord_alert")
@patch("pipeline.generate_strategy_code")
@patch("pipeline.discover_patterns")
@patch("pipeline.compute_features")
@patch("pipeline.collect_bars")
@patch("pipeline.get_redis")
@patch("pipeline.load_config")
def test_pipeline_passes_claude_settings_to_codegen(
    mock_cfg, mock_redis, mock_collect, mock_features,
    mock_discover, mock_codegen, mock_alert, mock_ensure,
    mock_save_strat, mock_save_run,
):
    from pipeline import _run_phases

    mock_cfg.return_value = {"ml": {
        "symbols": ["AAPL"],
        "lookback_days_momentum": 365,
        "lookback_days_regime": 1825,
        "max_strategies_per_run": 5,
        "min_forward_return_pct": 1.5,
        "min_examples": 30,
        "min_win_rate_pct": 45.0,
        "codegen_provider": "claude",
        "codegen_model": "claude-sonnet-4-5",
    }}
    mock_redis.return_value = _make_mock_redis(
        provider=b"claude", claude_model=b"claude-sonnet-4-5"
    )
    mock_collect.return_value = {"AAPL": []}
    mock_features.return_value = [{"bar_date": None}]

    from discoverer import CandidatePattern
    pattern = CandidatePattern("decision_tree", "rsi <= 30", 40, 2.0, 55.0, 0.8, "AAPL")
    mock_discover.return_value = [pattern]
    mock_codegen.return_value = "def generate_signal(s): return {'decision':'hold','confidence':0.5,'reasoning':'x'}"
    mock_save_strat.return_value = 1
    mock_save_run.return_value = None

    _run_phases()

    _, kwargs = mock_codegen.call_args
    assert kwargs.get("provider") == "claude"
    assert kwargs.get("model") == "claude-sonnet-4-5"


@patch("pipeline.save_ml_run")
@patch("pipeline.save_ml_strategy")
@patch("pipeline.ensure_ml_tables")
@patch("pipeline._send_discord_alert")
@patch("pipeline.generate_strategy_code")
@patch("pipeline.discover_patterns")
@patch("pipeline.compute_features")
@patch("pipeline.collect_bars")
@patch("pipeline.get_redis")
@patch("pipeline.load_config")
def test_pipeline_passes_gemini_settings_to_codegen(
    mock_cfg, mock_redis, mock_collect, mock_features,
    mock_discover, mock_codegen, mock_alert, mock_ensure,
    mock_save_strat, mock_save_run,
):
    from pipeline import _run_phases

    mock_cfg.return_value = {"ml": {
        "symbols": ["AAPL"],
        "lookback_days_momentum": 365,
        "lookback_days_regime": 1825,
        "max_strategies_per_run": 5,
        "min_forward_return_pct": 1.5,
        "min_examples": 30,
        "min_win_rate_pct": 45.0,
        "codegen_provider": "claude",
        "codegen_model": "claude-sonnet-4-5",
    }}
    mock_redis.return_value = _make_mock_redis(
        provider=b"gemini", gemini_model=b"gemini-2.0-flash"
    )
    mock_collect.return_value = {"AAPL": []}
    mock_features.return_value = [{"bar_date": None}]

    from discoverer import CandidatePattern
    pattern = CandidatePattern("decision_tree", "rsi <= 30", 40, 2.0, 55.0, 0.8, "AAPL")
    mock_discover.return_value = [pattern]
    mock_codegen.return_value = "def generate_signal(s): return {'decision':'hold','confidence':0.5,'reasoning':'x'}"
    mock_save_strat.return_value = 1
    mock_save_run.return_value = None

    _run_phases()

    _, kwargs = mock_codegen.call_args
    assert kwargs.get("provider") == "gemini"
    assert kwargs.get("model") == "gemini-2.0-flash"
```

- [ ] **Step 2: Run tests to confirm they fail**

```bash
docker-compose -f docker-compose.yml exec -T ml python -m pytest tests/test_pipeline_codegen.py -x -q 2>&1 | tail -10
```

Expected: ImportError or AssertionError (`_run_phases` doesn't read from Redis yet).

- [ ] **Step 3: Update pipeline.py**

Add `get_redis` to the imports in `services/ml/pipeline.py` (after the existing `from shared.config import load_config` line):

```python
from shared.redis_client import get_redis
```

Replace the Phase 4 setup block in `_run_phases()`. Find:

```python
        # Phase 4: Strategy codegen
        log.info("Phase 4: Generating strategy code for %d patterns", patterns_found)
        anthropic_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        saved_strategies: list[tuple[int, Optional[str]]] = []

        for pattern in patterns:
            code = generate_strategy_code(pattern, client=anthropic_client)
```

Replace with:

```python
        # Phase 4: Strategy codegen
        log.info("Phase 4: Generating strategy code for %d patterns", patterns_found)

        r = get_redis()
        _codegen_provider = r.get("config:ml_codegen_provider") or ml_cfg.get("codegen_provider", "claude")
        if isinstance(_codegen_provider, bytes):
            _codegen_provider = _codegen_provider.decode()

        if _codegen_provider == "gemini":
            _model_key = "config:ml_codegen_gemini_model"
            _default_model = "gemini-2.0-flash"
        else:
            _model_key = "config:ml_codegen_claude_model"
            _default_model = ml_cfg.get("codegen_model", "claude-sonnet-4-5")

        _codegen_model = r.get(_model_key) or _default_model
        if isinstance(_codegen_model, bytes):
            _codegen_model = _codegen_model.decode()

        log.info("Phase 4 codegen: provider=%s model=%s", _codegen_provider, _codegen_model)

        if _codegen_provider == "gemini":
            _codegen_client = None
        else:
            _codegen_client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

        saved_strategies: list[tuple[int, Optional[str]]] = []

        for pattern in patterns:
            code = generate_strategy_code(
                pattern,
                client=_codegen_client,
                provider=_codegen_provider,
                model=_codegen_model,
            )
```

- [ ] **Step 4: Run pipeline codegen tests**

```bash
docker-compose -f docker-compose.yml exec -T ml python -m pytest tests/test_pipeline_codegen.py -q 2>&1 | tail -10
```

Expected: `2 passed`

- [ ] **Step 5: Run full ML test suite**

```bash
docker-compose -f docker-compose.yml exec -T ml python -m pytest tests/ -q 2>&1 | tail -10
```

Expected: all tests pass.

- [ ] **Step 6: Rebuild ML container and verify health**

```bash
docker-compose -f docker-compose.yml up -d --build ml
docker-compose -f docker-compose.yml exec -T ml curl -s http://localhost:8082/health
```

Expected: `{"status": "ok"}`

- [ ] **Step 7: Commit**

```bash
git add services/ml/pipeline.py services/ml/tests/test_pipeline_codegen.py
git commit -m "feat: ML pipeline reads codegen provider/model from Redis at runtime"
```
