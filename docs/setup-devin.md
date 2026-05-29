# Set Up AgentPool For Devin CLI

Use this when you want AgentPool to inspect Devin CLI readiness and usage.

AgentPool will not log in to Devin, store credentials, scrape browser pages, or
edit Devin config files.

## Run The Guided Check

```bash
agentpool setup devin-cli
```

Machine-readable output:

```bash
agentpool setup devin-cli --json
```

Skip live usage probing:

```bash
agentpool setup devin-cli --skip-usage
```

The setup checks:

- whether `devin` is installed;
- model defaults and model catalog metadata;
- native Devin usage from existing CLI auth where available.

## Usage Notes

Devin usage is reported as provider-specific quota windows when available:
included daily quota, weekly quota, and on-demand balance if the provider API
returns them. Unknown fields stay unknown instead of being inferred.

## Use Devin Through AgentPool

```bash
agentpool usage-summary --provider devin-cli --refresh --json
agentpool models --provider devin-cli
agentpool spawn \
  --provider devin-cli \
  --repo . \
  --task "Inspect this repo read-only and summarize the main entry points." \
  --isolation read_only
```
