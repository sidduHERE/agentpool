# Provider Model Catalog

AgentPool keeps model choice explicit. The catalog only supplies defaults after
a caller has already selected a provider such as `codex-cli` or `droid-cli`.
It is not a ranking table and it must not be used to implement `provider=auto`.

## Inspect

```bash
agentpool models
agentpool models --provider droid-cli
agentpool models --provider codex-cli --json
agentpool models validate --path ~/.agentpool/models.json
```

Each provider entry can describe:

- `default_model`: used by `spawn` when `--model` is omitted.
- `smoke_model`: used by guarded real-provider smoke tests.
- `models`: known model slugs, display names, aliases, confidence, and metadata.
- `model_arg`: the CLI flag used by providers with native model selection.
- `model_selection`: non-flag pinning mode, such as Droid runtime settings.
- `submit_keys`: tmux keys needed for interactive submit quirks.
- `quirks`: operator-facing notes for harness behavior.

## Override

The embedded catalog lives inside the package as
`agentpool/provider_model_catalog.json`. Model catalog overlays are JSON-only so
metadata stays unambiguous; for example, `"off"` remains a string instead of
becoming a YAML boolean. Users can layer JSON catalogs through
`~/.agentpool/config.yaml`:

```yaml
model_catalog_paths:
  - ~/.agentpool/models.json
```

Example override:

```json
{
  "version": 1,
  "providers": {
    "droid-cli": {
      "default_model": "glm-5.1",
      "smoke_model": "glm-5.1",
      "model_selection": "runtime_settings",
      "models": [
        {
          "id": "glm-5.1",
          "display_name": "Droid Core GLM-5.1",
          "source": "config",
          "confidence": "user_configured",
          "metadata": {
            "reasoning": {
              "supported": ["off", "high"],
              "default": "high"
            }
          }
        }
      ]
    }
  }
}
```

Direct provider config still wins after catalog loading:

```yaml
providers:
  codex-cli:
    metadata:
      default_model: gpt-5.3-codex
      smoke_model: gpt-5.3-codex
```

This lets a user or a primary agent pin a local default without changing the
provider adapter. Explicit `--model` on `agentpool spawn` or `agentpool smoke`
still overrides both catalog and provider config.

Compatibility note: the PRD calls the Factory coding product `factory-droid`,
but AgentPool exposes it as `droid-cli` because the installed command is
`droid`. Keep the catalog keyed by `droid-cli` unless a separate Factory Droid
binary or harness appears.

## Droid

Droid interactive mode does not expose a native `--model` flag. AgentPool pins
the configured or requested model by writing a process-local settings file under
`~/.agentpool/runtime-settings/` and launching Droid with `--settings <path>`.
This avoids mutating the user's global Factory settings and avoids accidentally
using a custom default model backed by a local proxy.

## Catalog Confidence

Model lists are conservative. Providers that expose a complete local list, such
as Droid through `droid exec --help`, are marked as observed. Providers whose
CLI only exposes examples or aliases are marked with lower catalog completeness.
When in doubt, prefer a user catalog override rather than guessing.
