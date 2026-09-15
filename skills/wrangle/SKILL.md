---
name: wrangle
description: Drive another coding agent headlessly from wherever you already are. Use when someone wants to run a prompt through a specific model or harness ("run this with Astra", "get a second opinion from Gemini Flash", "what would Codex say"), compare several models on the same task, or pick the cheapest way to reach a model. Knows the CLI shapes, failure modes and cost traps of claude, opencode, agy (Antigravity) and codex, and ships a hardened wrapper that normalizes all four behind one interface. Also handles "which harness do I need for model X" and how to install it.
allowed-tools: Bash, Read, Grep, Glob, AskUserQuestion
---

# wrangle

Run a prompt through another harness without leaving the one you are in.

Four CLIs can drive a model headlessly on a Mac: `claude`, `opencode`, `agy`
(Antigravity) and `codex`. They differ on every detail that matters, and several
of those details cost real money when you get them wrong. This skill carries the
knowledge, and `scripts/wrangle.py` carries the mechanics.

```bash
S=~/.agents/skills/wrangle/scripts/wrangle.py

python3 $S doctor                       # what is installed, authed, missing
python3 $S resolve astra                # every route to a model, ranked, with prices
python3 $S run --model astra --prompt "..."
python3 $S compare --models "astra,gemini flash,opus" --prompt "..."
python3 $S models astra                 # who serves it, including who you cannot reach
python3 $S prefs set astra codex:gpt-6-astra
python3 $S selftest
```

## The one rule: rank, then let the human choose

Cheapest-first is a **ranking hint, not a decision**. `resolve` lists every route
with the facts that separate them and the caller picks. This matters more than it
sounds: for some models every provider charges exactly the same, so an automatic
"cheapest" pick is a coin flip that silently hides the `-pro` and `-fast` variants
and the one with a smaller context window.

Order of precedence when choosing a route:

1. `--route` passed explicitly
2. a remembered preference (`prefs set`)
3. `--pick cheapest|fastest|first` for non-interactive callers
4. otherwise **ask**, showing `resolve` output

An alias names a *family*, never one pinned id. `astra` resolves to every Astra
variant so the caller sees that `-pro` exists. An alias that collapses variants is
a bug.

## Cost rungs

| Rung | Meaning | Examples |
|---|---|---|
| `subscription` | included in a plan you already pay for | claude on your plan, codex on ChatGPT auth, agy on Google login |
| `direct` | a first-party API key, billed per token | `google/`, `openai/`, `cerebras/` |
| `gateway` | a reseller, billed per token | OpenRouter, OpenCode Zen |

A subscription rung still shows the model's list price, labelled `(list)`, so you
can see what the same call would cost elsewhere.

## Doing the work

**Someone names a model.** Run `resolve`. If exactly one route exists, use it. If
several do, show the table and ask, unless a preference is already remembered.
Offer to remember the answer with `prefs set`.

**Someone wants a second opinion or a comparison.** Use `compare`. It runs the
routes in parallel and returns one envelope per model. It refuses to start when the
estimated cold-start overhead exceeds `--confirm-over` (default $1) without
`--yes`, because fan-out multiplies that overhead by the number of models.

**Someone names a model nothing installed can reach.** `resolve` exits 3 and lists
the providers that *do* serve it. `models <query>` shows the same with a `+` against
the ones reachable today. Installation is always Homebrew:

| CLI | Install | Auth |
|---|---|---|
| claude | `brew install --cask claude-code` | `claude` then `/login` |
| codex | `brew install codex` | `codex login` |
| agy | `brew install antigravity-cli` | run `agy` once, Google sign-in |
| opencode | `brew install opencode` | `opencode auth login` |

Prefer the harness whose vendor owns the model (`agy` for Gemini, `codex` for
OpenAI, `claude` for Claude) when it is on a subscription rung, because that route
is both cheapest and least likely to hit a schema mismatch. `opencode` is the
universal fallback and the only way to reach GLM, Kimi, Qwen, Grok and similar.

## Permissions and effort are translated, not forwarded

Each CLI spells these differently and two of them fail hard on the wrong value, so
`wrangle` owns the vocabulary and maps it per CLI.

| Request | claude | agy | opencode | codex |
|---|---|---|---|---|
| `--read-only` | `--permission-mode plan` | `--mode plan` | `--agent plan` | `-s read-only` |
| `--write` | `--permission-mode acceptEdits` | `--mode accept-edits` | default | `-s workspace-write` |
| `--yolo` | `--dangerously-skip-permissions` | same | `--auto` | `-s danger-full-access` |

**The default denies writes on every CLI**, including codex, whose own default is
`workspace-write` with `approval: never`. A `compare` across four models asks a
question; it must not edit your tree to answer it.

`--effort` uses claude's five-step scale and is clamped to what each CLI accepts
(agy and codex cap at `high`; opencode has no effort flag and uses `--variant`).
A step-down is reported in `notes`, never silent. On agy the tier lives in the
model id, so an effort request rewrites the id instead of adding a flag.

## The result envelope

Every run returns the same shape regardless of which CLI produced it, which is what
makes this composable:

```json
{"ok": true, "cli": "agy", "model": "gemini-3.8-flash-medium",
 "route": "agy:gemini-3.8-flash-medium", "rung": "subscription",
 "text": "...", "exit_code": 0, "duration_s": 4.2, "cost_usd": null,
 "usage": {...}, "session_id": "...", "error": null,
 "raw_path": "~/.config/wrangle/runs/...log", "argv": [...]}
```

`notes` carries anything the wrapper changed on your behalf: an effort step-down,
an agy tier rewrite, a `--read-only` that an explicit `--agent` overrode.

`cost_usd` is `null` when genuinely unknown, never `0`. A subscription run has no
per-call cost; that is not the same as free work.

Exit codes: `0` ok, `1` the agent ran and failed, `2` usage, `3` no route,
`4` prerequisite missing, `5` timeout, `6` internal.

## Portability

This skill ships **no account-specific facts**. Which models your plan serves, which
providers you are authed to and which routes you prefer are discovered at runtime
and cached in `~/.config/wrangle/`, never in the skill directory. That is what makes
it shareable. When you learn a per-account fact, write it to local state, not here.

`doctor --probe` discovers plan entitlements by making a cheap call per model.
Rejections are free and are cached so a rejected model stops being offered.

## Reference

Read these before changing how a CLI is invoked. Each documents flags verified
against the installed binary, not the vendor's docs.

- [`reference/capability-matrix.md`](reference/capability-matrix.md) - all four CLIs side by side
- [`reference/gotchas.md`](reference/gotchas.md) - **the traps, each with its evidence**
- [`reference/model-routing.md`](reference/model-routing.md) - how a name becomes a command
- [`reference/claude-code.md`](reference/claude-code.md) · [`opencode.md`](reference/opencode.md) · [`agy.md`](reference/agy.md) · [`codex.md`](reference/codex.md)

## Extending

`wrangle.py` is stdlib-only Python with no network calls of its own. To add a CLI,
write a `run_<cli>` returning `(Result, stdout, stderr)`, register it in `RUNNERS`,
and add a discovery method to `Router`. Run `selftest` after any change; it covers
the stream decoder, the ranking, the plan-gap detector and the timeout killer.
