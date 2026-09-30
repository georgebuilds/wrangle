# CLI notes

Use the installed CLI's help when flags differ. These command shapes were checked
on 2026-09-30; model availability still belongs to the local account.

## Roster discovery

`scripts/roster.py` requests Claude Code's control-protocol `initialize` response,
runs `agy models`, and calls Codex app-server's paginated `model/list`. It retains
only model IDs, labels, resolved IDs, and effort options, never account fields.
Claude discovery disables hooks and MCP servers and sends no user message. Codex
discovery creates no thread or turn. Commands run outside the working repository.
The helper uses the installed clients and their existing login/configuration;
it does not upgrade them or probe model entitlements. If a client's discovery
protocol changes, the failure stays visible and its old cache remains dated.

Protocol references: [Claude SDK initialization](https://github.com/anthropics/claude-agent-sdk-python/blob/main/src/claude_agent_sdk/_internal/query.py)
and [Codex model listing](https://developers.openai.com/codex/app-server/#list-models-modellist).

## Claude Code

- `claude -p PROMPT --model sonnet --output-format json` runs once.
- `claude auth status` should report `loggedIn: true` and `authMethod: claude.ai`.
  The runner checks this without printing account identity. Login is `claude auth login`.
- API keys, provider overrides, and `apiKeyHelper` settings can change billing.
  The runner refuses common environment overrides; also inspect active settings if
  status is unexpected. Never dump credential files. Do not use `--bare` to optimize
  a subscription call: it is intended for API-key execution.
- `--permission-mode plan` is the read-only mode; `acceptEdits` permits edits.
- Native model aliases follow their family. Use an exact ID only when requested.
  Effort is model-dependent; the installed help lists `low|medium|high|xhigh|max`.
- The final JSON record has `type: result`, `result`, `is_error`, `usage`, and
  `total_cost_usd`. That cost is a usage valuation, not a subscription invoice.
- For follow-up context, use `--resume SESSION_ID`; a fresh call starts a fresh conversation.

See [subscription authentication](https://support.claude.com/en/articles/12304248-manage-api-key-environment-variables-in-claude-code)
and the [CLI reference](https://code.claude.com/docs/en/cli-reference).

## agy (Antigravity)

- Run `agy` interactively once to sign in with Google if needed.
- `agy models` lists available IDs and labels. The call can be slow; reuse its output
  during the session instead of fetching it before every run.
- `agy -p PROMPT --model MODEL_ID --output-format json --print-timeout 900s` runs once.
- `--mode plan` is the read-only mode; `--mode accept-edits` permits edits.
- Some model IDs end in `-low`, `-medium`, or `-high`. Choose an available ID at the
  desired tier. Do not also pass `--effort` with a tiered ID: versions of agy reject
  conflicting selections. For an untiered ID, consult help/model support before
  passing effort; the current help lists `low|medium|high|max`.
- The current timeout default is `0` (unlimited); older versions defaulted to five
  minutes. Always set an explicit deadline. The runner also enforces a host deadline.
- JSON success has `status: SUCCESS`, `response`, `usage`, and `conversation_id`.
  Otherwise read `error`; do not turn an error response into a model opinion.
- `--conversation ID` resumes a specific conversation. Avoid `--continue` when
  concurrent calls could make "the latest conversation" ambiguous.
- agy may offer other model families. Use them when the person explicitly chooses
  agy, rather than inferring that a Gemini subscription entitles every model.

## Codex

- `codex login status` should say `Logged in using ChatGPT`; login is `codex login`.
- `codex exec --json -s read-only --ephemeral -o ANSWER_FILE PROMPT` runs once.
  `--skip-git-repo-check` allows a non-repository working directory.
- The runner selects `model_provider="openai"` and `forced_login_method="chatgpt"`
  for the child process. It does not rewrite the person's config or fall back to a key.
- Omit `-m` for the configured default. A family nickname must be resolved to a native
  ID before passing it; an unrecognized model can fail only after the request starts.
  A "not supported ... with a ChatGPT account" error describes account availability.
- `-s workspace-write` permits implementation. Read-only is explicit for reviews.
- Effort is `-c 'model_reasoning_effort="VALUE"'`; preserve supported requested values
  instead of assuming every model has the same ceiling.
- JSONL has `item.completed` / `agent_message` for text, `turn.completed` for usage,
  and `error` or `turn.failed` on failure. The answer file may be absent on failure.
- Close stdin (`</dev/null`) when supplying a prompt argument. A hanging inherited
  pipe can otherwise block the process. The runner closes it automatically.
- `--ephemeral` means runner sessions cannot be resumed. Use native persistent
  `codex exec` then `codex exec resume SESSION_ID` when continuation is needed.

See the [configuration reference](https://developers.openai.com/codex/config-reference/).
