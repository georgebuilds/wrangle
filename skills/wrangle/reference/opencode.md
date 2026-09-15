# opencode

Verified against `opencode 1.18.31`. The universal fallback: the only one of the
four that reaches GLM, Kimi, Qwen, Grok and most gateway-hosted models.

## Headless shape

```bash
opencode run -m openrouter/openai/gpt-6-astra --format json "PROMPT" </dev/null
```

Models are always `provider/model`. With OpenRouter the provider segment itself
contains a slash: `openrouter/openai/gpt-6-astra`.

## Output

A whitespace-separated **stream** of objects on success, but a **single** object on
error. Parse with a repeated `raw_decode`, never a single `json.loads`.

Concatenate `text` events for the answer. `step_finish` carries `cost` and
`tokens`.

## Flags worth knowing

| Flag | Why |
|---|---|
| `-m provider/model` | required, no aliases |
| `--format default\|json` | |
| `--variant high\|max\|minimal` | provider-specific reasoning effort |
| `--agent NAME` | restricts tools; **`plan` is built in and read-only** |
| `--dir DIR` | working directory |
| `--auto` | auto-approve permissions |
| `-c`, `-s SESSION`, `--fork` | resume |
| `-f` / `--file` | attach files |
| `--share` | publish the session |
| `--pure` | skip external plugins (does **not** drop MCP servers) |

There is no read-only flag; the built-in `plan` agent is the equivalent, which is
what `wrangle --read-only` selects. There is no timeout flag and no
structured-output flag. opencode is the only one of
the four with no `--json-schema` equivalent; emulate it by instructing the model and
validating the result yourself.

## The cost trap

opencode sends every configured MCP server's tools on every call. Measured: 352
declarations, 103,972 cache-write tokens, $1.30 for a three-token prompt on a $10/M
model. Config isolation via `--pure`, `OPENCODE_CONFIG`, `OPENCODE_CONFIG_DIR` and
`OPENCODE_CONFIG_CONTENT` all failed to reduce it. Read
[gotchas](gotchas.md#opencode-sends-your-entire-mcp-tool-surface-on-every-call)
before using opencode for fan-out.

The remaining documented lever is `--agent` with a restricted `tools` map.

## Providers and auth

`opencode auth login` adds credentials; they land in
`~/.local/share/opencode/auth.json`, whose top-level keys are the provider ids.
Those keys are exactly the set of providers you can route to, which is how
`wrangle doctor` reports them.

`opencode models` lists everything the catalog knows, including providers you have
no credentials for, so filter by your authed set before offering a route.

## The catalog

opencode maintains a models.dev cache at `~/.cache/opencode/models.json`, roughly
4.6MB, covering 217 providers and 7,825 models with per-model cost, context limit,
reasoning and tool-call support. It is the best offline pricing source on the
machine and `wrangle` reads it directly. Installing opencode is worth it for this
file alone.
