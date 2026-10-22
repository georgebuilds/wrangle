---
name: wrangle
description: Use Claude Code, agy (Antigravity), and Codex through existing subscriptions without leaving the current coding harness. Use for cross-model second opinions, comparisons, delegated work, and choosing which of these CLIs should run a task. Works from OpenCode too; never launches OpenCode headlessly.
allowed-tools: Bash, Read, Grep, Glob, AskUserQuestion
---

# wrangle

Stay in the current harness and send work to Claude Code, agy, or Codex using the
person's existing plans. OpenCode can be the host invoking this skill; it is never
an execution target. Do not look up OpenCode providers, API rates, or gateway routes.

## Choose the target

Honor an explicit harness first. Otherwise use the model's native harness:

| Requested model | Harness | Model selection |
|---|---|---|
| Claude, Opus, Sonnet, Haiku, Fable | `claude` | Use a native alias such as `sonnet`, or preserve an exact model ID |
| Gemini, Flash, Gemini Pro | `agy` | Choose the matching ID from the refreshed roster |
| GPT, Astra, Sol, or another OpenAI model | `codex` | Use the refreshed roster to resolve a family name; preserve exact IDs |

If only a harness is named, omit `--model` and use its configured default. For an
unspecified second opinion, use another available subscription harness, announce
which, and proceed. Ask only if ambiguity would materially change the requested
work. An explicit model that is unavailable is a reason to report the limitation,
not silently substitute another model or buy API usage.

Use discovery only where needed. Claude's native aliases avoid pinning versions.
Use the roster below for family-name resolution and questions about available models.
Metadata is a hint, not proof of plan entitlement. Do not invent aliases or probe
every model.

## Keep the roster current

Locate `scripts/roster.py` relative to this skill. It queries the native model lists
without sending a prompt or starting an inference turn:

```bash
python3 /path/to/wrangle/scripts/roster.py                 # all three harnesses
python3 /path/to/wrangle/scripts/roster.py agy             # only the needed harness
python3 /path/to/wrangle/scripts/roster.py --refresh       # force refresh now
python3 /path/to/wrangle/scripts/roster.py --offline       # cached lists only
```

Normal reads refresh missing lists and lists older than 24 hours. Force a refresh
after a CLI update, account change, model-availability failure, or a request for
the latest roster. This runs on demand; there is no background job to install.
Match family names against returned IDs/names and use returned effort options.

Lists live in `~/.config/wrangle/roster/` (or `$WRANGLE_HOME/roster/`), outside the
shared skill. Each has a fetch timestamp. Failed refreshes retain the last good
list with `stale: true` and an error; never describe that list as current. Exit 1
can mean a partial result: inspect each harness. Missing CLIs do not hide the others.
Native CLIs may themselves cache metadata; this reports their current roster, not
a guarantee that a newly announced model is already available to the account.

## Run the work

Read [CLI notes](reference/cli-notes.md) for the chosen harness. Locate
`scripts/wrangle.py` relative to **this SKILL.md**, not the working repository.
The runner takes a harness and optional native model name. It handles deadlines,
closed stdin, subscription checks for Claude/Codex, and JSON result extraction.

```bash
# S is the absolute path to this skill's scripts/wrangle.py.
python3 "$S" claude --model sonnet --cwd /path/to/repo --prompt-file /tmp/question.txt
python3 "$S" codex --cwd /path/to/repo --prompt-file /tmp/task.txt --write
python3 "$S" agy --model <id-from-agy-models> --cwd /path/to/repo --prompt-file /tmp/question.txt
```

`--file path` appends a text file's contents; it is repeatable. Prompts also accept
`--prompt` or stdin. Prefer prompt files for long text or shell-sensitive content.
`--timeout` defaults to 900 seconds. `--dry-run` shows the command without executing
or checking auth. `--effort` passes a native value unchanged; omit it unless requested
or needed. On agy, select a tiered model ID instead of adding a conflicting effort flag.

Results are JSON. Answers up to 4 KiB stay in `text`; larger answers are written to
a private temporary `.txt` file, and the JSON contains `file`, `bytes`, and `lines`
instead of reprinting the answer. Read only the relevant parts, or pass the file
directly to another run with `--file` or `--prompt-file`. Use `--output-dir <scratchpad>`
to keep large answers in a chosen scratch directory; otherwise the runner uses the
system temporary directory. `--inline` forces the full answer into JSON when needed.

The default is plan/read-only mode. Use `--write` for authorized implementation.
These modes are CLI permission controls, not a guarantee against every configured
hook or external tool side effect. Do not bypass a refusal by broadening permissions.
Direct native invocation is fine for features the small runner does not expose,
such as resuming a session; preserve the same auth, scope, and deadline constraints.

## Pass context between models

A new process does not inherit this conversation. Give it the objective, relevant
files or diff, constraints, expected output, and whether it may edit. Set `--cwd`
to the actual repository so its project instructions load. Do not send credentials.

For comparisons, give each model the same question and evidence independently,
then reconcile agreements and disagreements against the code. Run independent
read-only calls concurrently when useful. For a chain, pass the relevant output
from one stage to the next, using the returned `file` path for large answers instead
of echoing them into the conversation. For example, pass Claude's plan to Codex for implementation,
then the resulting diff to Gemini for review. Serialize writes to the same tree or
use separate worktrees. A second opinion alone does not authorize applying changes.

Inspect `ok` and `error`, not just whether text exists. Summarize the findings and
name the harness/model that produced them. `reported_cost_usd` is the CLI's reported
usage valuation, not proof of an extra subscription charge. Unknown cost stays null.
On auth, quota, or model availability failures, report the actual issue. Use an
alternative only if the request leaves that choice open; never fall back to paid APIs.

## Scope and maintenance

Keep account details, plan entitlements, and personal defaults out of the shared
skill. The runner uses installed login state; only the roster helper caches model metadata.
It has no price ranking, provider discovery, automatic fallback, or OpenCode runner.
Do not enable extra usage or change account/billing settings to complete a call.

Validate edits with `python3 -m unittest discover -s scripts -p 'test_*.py'` from this
skill directory. Tests use fake CLI output and make no model calls.
