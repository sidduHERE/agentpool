# AgentPool stats

`agentpool stats` aggregates pool activity over a configurable time window. The
same logic is available on the CLI and the opt-in MCP `stats` toolset
(`get_stats`, `get_stats_card`). Stats resources are not part of the lean MCP
surface.

## Window

Default window is the last **7 days**. Override with:

- `--since 30d`, `--since 12h`, `--since 1w`, `--since all`
- `--from ISO --to ISO` (mutually exclusive with `--since`)
- MCP: `get_stats(window="30d")`

## Scope

CLI defaults to `scope=all`. MCP defaults to `scope=mine` because MCP hosts construct
`SessionManager(scope_sessions_by_coordinator=True)`. The JSON always echoes the scope
that was applied.

## Metrics

### Sessions

Counts sessions created in the window, grouped by provider, role, and state. Event
totals include `spawn`, `terminate`, `interrupt`, and `timeout`.

### Parallelism

- `wall_clock_hours`: elapsed time from earliest session start to latest session end
- `sum_worker_hours`: sum of per-session durations
- `ratio`: `sum_worker_hours / wall_clock_hours`
- `peak_concurrent` / `peak_at`: sweep-line peak overlap

### Walls

<a id="walls"></a>

**Definition:** for each `spawn` event in the window with provider `P`:

1. For every configured provider `Q`, fetch the most recent `usage_snapshot` with
   `ts <= spawn_ts` and age `<= 2 * policy.usage_stale_after_seconds` (1h default when
   stale-after is 30m). If none, mark `Q` as unknown.
2. Re-run the existing `usage.summary._usable_reason(...)` on each snapshot using
   config `min_remaining_percent`. This is the single source of truth for usability.
3. Classify:
   - `wall_avoided` if `P` is usable **and** at least one other configured provider is
     unusable for **quota reasons** (`limit_reached`, `near_limit`,
     `*_below_*_percent`) — not auth/not-installed failures.
   - `wall_hit` if `P` is unusable for quota reasons at spawn time.
4. If `>50%` of spawns have unknown neighbor snapshots, set `walls.confidence = "low"`
   and add `walls_low_confidence` to `data_quality`.

Walls are an observation about provider distribution, not guidance to bypass quotas.

### Quota

Per-provider min/max/current remaining percent from usage snapshots sampled in the
window.

### Utilization

`subscription_utilization = sum(worker_hours) / sum(usable_hours_in_window)` where
usable hours approximates `window_hours * providers_with quota samples`.

### Tokens

Only providers with ccusage-backed token telemetry (currently `claude-code`) populate
token counts. Other providers are listed under `providers_without_token_data`.

## Data quality

`data_quality` is always an array. When a metric is null or partial, an entry explains
why. Never invent zero token counts for providers without telemetry.

| Code | Meaning |
|------|---------|
| `no_usage_data_in_window` | No usage snapshots for wall inference |
| `no_usage_data_for_provider` | Provider missing quota samples in window |
| `walls_low_confidence` | >50% spawns lacked fresh neighbor snapshots |
| `tokens_partial` | Token counts limited to active ccusage block |

## Caching

Stats are computed on demand. Two SQLite indexes keep aggregation cheap:

- `idx_sessions_created_at`
- `idx_events_ts`

A future `stats_snapshots` cache table with TTL is deferred.

## Share cards

`agentpool stats --share [PATH]` renders a 1200×630 PNG when the optional `card` extra
is installed (`pip install 'agentpool[card]'`).

## Schema

Frozen at `stats/v1`. Bump the version for any breaking JSON change.
