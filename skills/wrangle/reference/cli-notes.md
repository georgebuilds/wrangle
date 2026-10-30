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
- `-s workspace-write` permits implementation. Read-only is explicit for reviews. Neither
  can write under `.git`: a `git commit` fails on `.git/index.lock`, so commit from the host.
- Effort is `-c 'model_reasoning_effort="VALUE"'`; preserve supported requested values
  instead of assuming every model has the same ceiling.
- Speed is `-c 'service_tier="fast"'` or `"ultrafast"`, a config key with no CLI flag of
  its own and no entry in the model list. `~/.codex/config.toml` may already set it; a
  `-c` override wins for that run only.
- JSONL has `item.completed` / `agent_message` for text, `turn.completed` for usage,
  and `error` or `turn.failed` on failure. The answer file may be absent on failure.
- Close stdin (`</dev/null`) when supplying a prompt argument. A hanging inherited
  pipe can otherwise block the process. The runner closes it automatically.
- `--ephemeral` means runner sessions cannot be resumed. Use native persistent
  `codex exec` then `codex exec resume SESSION_ID` when continuation is needed.

See the [configuration reference](https://developers.openai.com/codex/config-reference/).

## Where each harness keeps its threads

What `scripts/threads.py` reads, all read-only. Paths are under `$HOME` unless an
environment variable moves them (`CODEX_HOME`, `CLAUDE_CONFIG_DIR`, `GEMINI_HOME`,
`XDG_DATA_HOME`); `WRANGLE_THREADS_HOME` replaces `$HOME` for tests.

- **Codex**: `~/.codex/state_N.sqlite` table `threads` is the index (id, `name`, `title`,
  `first_user_message`, `model`, `cwd`, `git_branch`, `rollout_path`, recency, `archived`,
  `thread_source`). The transcript is the rollout JSONL under `~/.codex/sessions/`
  (or `archived_sessions/`), either `.jsonl` or Zstandard-compressed `.jsonl.zst`.
  The index may still name the plain path after compression; the reader resolves
  its compressed sibling and streams it without changing the store. Reading `.zst`
  requires Python's `compression.zstd` (3.14+) or the `zstd` command on `PATH`;
  missing support and decompression failures are reported as errors. Records include
  `event_msg` / `item_completed` items of type `UserMessage` and `AgentMessage`, tool calls
  as `response_item` `custom_tool_call`. `response_item` `reasoning` carries
  `encrypted_content` only. `session_index.jsonl` and `history.jsonl` are older views of
  the same facts. `codex resume ID` continues it interactively. Headless, `codex exec fork ID PROMPT`
  starts a new thread from its whole history and leaves it untouched; `codex exec resume ID PROMPT`
  appends the turn to the person's thread, `--ephemeral` or not. A resume or fork that
  fails with `already has an active writer` means the thread is open in another Codex
  window; read its rollout with `threads.py show` instead, which needs no lock.
- **Claude Code**: `~/.claude/projects/<cwd slug>/<session>.jsonl`, one record a line:
  `user` and `assistant` with `message.content`, `cwd`, `gitBranch`, `timestamp`,
  `message.model`; `ai-title` / `custom-title` for the name; `isSidechain` marks subagent
  turns. `~/.claude/history.jsonl` lists typed prompts by `sessionId`. `claude --resume ID`,
  `claude -p --resume ID --fork-session PROMPT`; the fork writes a new session file and leaves
  the original byte for byte.
- **agy / Antigravity**: `~/.gemini/antigravity-cli/` (and `antigravity/` for the IDE):
  `conversation_summaries.db` holds title, preview, `workspace_uris`, times and
  `parent_conversation_id`; `history.jsonl` holds typed prompts with `conversationId`;
  `conversations/<id>.db` holds the body as protobuf blobs in `steps.step_payload`, which
  is not decoded. `~/.gemini/tmp/<project>/chats/session-*.json` are saved chats in plain
  JSON. `agy --conversation ID`, `agy --conversation ID -p PROMPT`; there is no fork, so a headless
  turn is appended to that conversation.
- **OpenCode**: `~/.local/share/opencode/opencode.db`: `session` (directory, title, model,
  `parent_id`, `time_archived`), `message.data` (role, model) and `part.data` (`text`,
  `reasoning`, `tool`). `opencode --session ID`; never run it headlessly from here.
