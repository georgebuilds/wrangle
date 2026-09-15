# wrangle

Drive `claude`, `opencode`, `agy` and `codex` headlessly from whichever one you are
already in.

Sometimes it is worth running something past a different model, whether for a second
perspective, a cheaper pass on bulk work, or because one harness is simply stronger
at the thing you are doing. Right now that means remembering which CLI can even
reach the model, switching over to it, and shuttling context back and forth by hand.

`wrangle` is a Claude Code skill that knows the real CLI shapes, failure modes and
cost traps of all four harnesses, plus a hardened wrapper that normalizes them
behind one interface, so "run this with Astra" just works. If nothing you have
installed can serve the model you asked for, it tells you what to install.

## Install

```
/plugin marketplace add georgebuilds/wrangle
/plugin install wrangle@wrangle
```

Then just ask, in your own words:

> run this past Gemini Flash and tell me what it would do differently

Or drive the wrapper yourself:

```bash
S=~/.claude/plugins/marketplaces/wrangle/skills/wrangle/scripts/wrangle.py

python3 $S doctor                    # what is installed, authed, missing
python3 $S resolve astra             # every route to a model, ranked, with prices
python3 $S run --model "gemini flash" --prompt "..."
python3 $S compare --models "astra,gemini flash,opus" --prompt "..."
```

## What it actually does

**Ranks routes, then lets you choose.** A model is usually reachable several ways at
different prices. `resolve` shows all of them with the facts that separate them and
you pick, because "cheapest" is frequently a tie and the tiebreakers are things only
you know:

```
5 route(s) for 'astra'

#  ROUTE                                         RUNG          IN/OUT $/Mtok (list)  CONTEXT
------------------------------------------------------------------------------------------
 1 codex:gpt-6-astra                             subscription  $10 / $50             1,050,000
     > plan entitlement unverified; run `wrangle doctor --probe`
 2 opencode:opencode/gpt-6-astra                 gateway       $10 / $50             1,050,000
     > ~$1.3 cold-start tool overhead
 3 opencode:openrouter/openai/gpt-6-astra-pro    gateway       $10 / $50             1,050,000
     > ~$1.3 cold-start tool overhead
```

An alias names a *family*, never one pinned id, so `-pro` and `-fast` variants stay
visible instead of being collapsed away.

**Returns one shape no matter who ran it.** That is what makes it composable:

```json
{"ok": true, "cli": "agy", "model": "gemini-3.8-flash-medium",
 "route": "agy:gemini-3.8-flash-medium", "rung": "subscription",
 "text": "...", "exit_code": 0, "duration_s": 4.2, "cost_usd": null,
 "usage": {...}, "notes": [], "raw_path": "...", "argv": [...]}
```

**Translates the vocabulary instead of forwarding it.** Each CLI spells permissions
and reasoning effort differently, and two of them fail hard on the wrong value:

| Request | claude | agy | opencode | codex |
|---|---|---|---|---|
| `--read-only` | `--permission-mode plan` | `--mode plan` | `--agent plan` | `-s read-only` |
| `--write` | `--permission-mode acceptEdits` | `--mode accept-edits` | default | `-s workspace-write` |
| `--yolo` | `--dangerously-skip-permissions` | same | `--auto` | `-s danger-full-access` |

The default denies writes on every CLI, including codex, whose own default is
`workspace-write` with `approval: never`.

## The traps it absorbs

Every one of these was reproduced against the installed binaries, not read from
vendor docs. Full detail with evidence in
[`gotchas.md`](skills/wrangle/reference/gotchas.md).

- **macOS has no `timeout(1)`.** Anything shelling out to it dies looking like an
  agent crash. Deadlines are enforced in-process and kill the whole process group.
- **agy silently truncates at five minutes.** `--print-timeout` defaults to `5m0s`.
- **agy refuses `--model` and `--effort` together**, because the reasoning tier is
  encoded in the model id. A tier change has to rewrite the id.
- **codex never creates its `--output-last-message` file on failure.** Absent is not
  the same as "the model said nothing".
- **codex accepts an unknown model id with only a warning** and fails later at the
  API, so you cannot validate a model by whether it starts.
- **opencode emits one JSON object on error but a stream of them on success.** A
  parser written against a failure breaks the first time the run works.
- **opencode sends your entire MCP tool surface on every call.** Measured with eight
  MCP servers: 352 tool declarations, 103,972 cache-write tokens, and **$1.30 for a
  three-token prompt** on a $10/M model. Fan-out multiplies it, so `compare`
  estimates the total and stops above a threshold.
- **Gemini rejects opencode's tool schema outright** for the same reason, which is
  why Gemini routes through agy instead.

## Requirements

macOS, Python 3.9+ (stdlib only, no pip install), and at least one of:

| CLI | Install | Auth |
|---|---|---|
| claude | `brew install --cask claude-code` | `claude` then `/login` |
| codex | `brew install codex` | `codex login` |
| agy | `brew install antigravity-cli` | run `agy` once, Google sign-in |
| opencode | `brew install opencode` | `opencode auth login` |

More installed means more routes. `wrangle doctor` reports what you have and gives
the install line for what you do not.

Pricing comes from the models.dev catalog that opencode maintains on disk, so it is
offline and free. Without opencode the routing still works and the price columns
read `?`.

## Privacy and portability

This ships **no account-specific facts**. Which models your plan serves, which
providers you are authed to, and which routes you prefer are discovered at runtime
and cached under `~/.config/wrangle/`, never in the repo. Nothing is sent anywhere
except to the CLI you asked for.

`wrangle doctor --probe` learns your plan entitlements by making one cheap call per
model. Rejections are free, cached, and expire so a plan upgrade is picked up.

## Development

The canonical skill lives at `~/.agents/skills/wrangle` ([openagents
layout](https://agents.md)); this repo is the distribution shell around it.

```bash
./sync.sh                                        # refresh skills/ from canonical, then validate
python3 skills/wrangle/scripts/wrangle.py selftest
claude plugin validate . --strict
```

`wrangle.py` is stdlib-only with no network calls of its own. To add a CLI, write a
`run_<cli>` returning `(Result, stdout, stderr)`, register it in `RUNNERS`, and add a
discovery method to `Router`. Run `selftest` after any change.

## License

MIT
