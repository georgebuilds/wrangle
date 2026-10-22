# wrangle

Use Claude Code, agy (Antigravity), and Codex through your existing subscriptions
without leaving the coding harness you are working in. OpenCode can host the
skill too; wrangle never launches OpenCode headlessly.

Ask for a second opinion, compare models, or pass work between them:

> Ask Argon to review this diff.

> Have Claude plan the change, then let Codex implement it.

Wrangle selects the native harness for the requested model, passes along the
context, and brings the result back. It does not compare API prices or fall back
to paid API routes when a subscription is unavailable.

## Install

As a Claude Code plugin:

```
/plugin marketplace add georgebuilds/wrangle
/plugin install wrangle@wrangle
```

For a standalone skill, copy `skills/wrangle/` into your harness's skill directory.
The installed copy used to maintain this repo lives at `~/.agents/skills/wrangle`.
Use either the plugin or a standalone copy in a given harness to avoid duplicates.

You need Python 3.9+ in a POSIX environment and at least one of `claude`, `agy`, or
`codex` installed and signed in to the corresponding plan. The scripts are tested
on macOS and use only the Python standard library.

## Routing

| Request | Default execution target |
|---|---|
| Claude models | Claude Code with your Claude subscription |
| Gemini models, including Argon | agy with your Google login |
| OpenAI models | Codex with your ChatGPT plan |

An explicit harness takes precedence. If you name only a harness, its configured
model default is used. Native model aliases and discovered IDs keep version
selection with the clients themselves. Model availability still depends on your
account.

## Keep the model roster current

The roster helper reads Claude Code's initialization metadata, `agy models`, and
Codex app-server's model list. It sends no inference prompts or entitlement probes.

From a clone of this repo:

```bash
python3 skills/wrangle/scripts/roster.py             # list all three harnesses
python3 skills/wrangle/scripts/roster.py codex       # list just Codex
python3 skills/wrangle/scripts/roster.py --refresh   # force a refresh
python3 skills/wrangle/scripts/roster.py --offline   # read the cache only
```

Normal reads refresh lists older than 24 hours. Force a refresh after updating a
CLI, changing accounts, or encountering a model-availability error. There is no
background service. Cached metadata lives in `~/.config/wrangle/roster/`, or under
`WRANGLE_HOME` when set. A failed refresh retains the previous list with its old
timestamp, a stale marker, and an error. Each CLI may also cache its own metadata.

## Run directly

The skill chooses the model; the runner takes its native ID or alias:

```bash
S=skills/wrangle/scripts/wrangle.py
python3 "$S" claude --model sonnet --cwd /path/to/repo --prompt-file /tmp/review.txt
python3 "$S" codex --cwd /path/to/repo --prompt-file /tmp/task.txt --write
python3 "$S" agy --model MODEL_ID_FROM_ROSTER --prompt 'Explain this approach'
```

`--file` appends a text file and can be repeated. `--timeout` defaults to 900 seconds.
`--dry-run` prints the command without executing it. `--effort` passes a native
value unchanged; use the model roster and CLI notes to choose a supported value.

The default is plan/read-only mode. `--write` permits authorized implementation.
The runner checks subscription login for Claude and Codex, closes stdin, enforces
a deadline, and returns JSON with `ok`, `error`, usage, and timing. Answers up to
4 KiB are included in `text`; larger answers are saved to a private temporary file
and returned as `file`, `bytes`, and `lines`. Use `--output-dir` to choose the
scratch directory, or `--inline` to include the full answer in JSON. A CLI's
`reported_cost_usd` is a usage valuation, not proof of an extra subscription charge.

For comparisons, the skill runs independent calls and reconciles their findings.
For sequential work, it passes the relevant result to the next model. Large
answers can be passed directly with `--file` or `--prompt-file`, without copying
them into the conversation. Writes to the same working tree are serialized or
separated into worktrees.

See [the skill](skills/wrangle/SKILL.md) for the workflow and
[CLI notes](skills/wrangle/reference/cli-notes.md) for invocation details.

### Upgrading from 0.1

The old `run`, `resolve`, `compare`, `doctor`, `models`, `prefs`, and `selftest`
subcommands have been removed. Run `wrangle.py HARNESS ...` for execution and
`roster.py` for model discovery. Comparisons are coordinated by the calling agent.
OpenCode execution, price ranking, automatic API fallback, and the custom alias
catalog are no longer part of wrangle. Existing local preferences are not read.

## Development

The canonical skill lives at `~/.agents/skills/wrangle`; this repo packages it for
distribution. Sync copies only the skill, not local model caches or account state.

```bash
./sync.sh
python3 -m unittest discover -s skills/wrangle/scripts -p 'test_*.py'
claude plugin validate . --strict
```

`sync.sh` copies the canonical skill, runs the local tests, and validates the plugin
when Claude Code is installed. Tests use fake CLI responses and spend no model
quota. To sync from another location, set `WRANGLE_SRC`.

## License

MIT
