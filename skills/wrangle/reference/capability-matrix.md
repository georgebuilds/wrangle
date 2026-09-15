# Capability matrix

All four CLIs side by side. Every row was verified against the installed binary by
running it, not read from vendor documentation. Versions at time of verification:
`claude 2.1.238`, `opencode 1.18.31`, `agy 1.2.3`, `codex 0.146.0` (macOS).

| | claude | opencode | agy | codex |
|---|---|---|---|---|
| headless entry | `-p` / `--print` | `run` | `-p` / `--print` | `exec` |
| prompt source | arg or stdin | arg | arg | arg or stdin |
| model flag | `--model` | `-m provider/model` | `--model` | `-m` / `--model` |
| model aliases | yes (`opus`, `sonnet`) | no, fully qualified | no, effort in the id | no |
| reasoning effort | `--effort low..max` | `--variant` | tier is **in the model id**; `--effort` beside it is a hard error | `-c model_reasoning_effort=` |
| JSON output | `--output-format json\|stream-json` | `--format json` | `--output-format json\|stream-json` | `--json` (JSONL) |
| output is | one object | **object on error, stream on success** | one object | JSONL stream |
| structured output | `--json-schema` | **none** | `--json-schema` | `--output-schema FILE` |
| final text to file | no | no | no | `-o FILE` |
| working dir | process cwd, `--add-dir` | `--dir` | `--add-dir` | `-C` / `--cd`, `--add-dir` |
| read-only mode | `--permission-mode plan` | `--agent plan` (built in) | `--mode plan` | `-s read-only` |
| bypass prompts | `--dangerously-skip-permissions` | `--auto` | `--dangerously-skip-permissions` | `-s danger-full-access` |
| default posture | prompts | prompts | prompts | **writes** (`workspace-write`, `approval: never`) |
| cost ceiling | `--max-budget-usd` | none | none | none |
| built-in timeout | none | none | `--print-timeout` (**default 5m**) | none |
| resume | `-r`, `-c`, `--fork-session` | `-c`, `-s`, `--fork` | `-c`, `--conversation` | `exec resume --last` |
| system prompt | `--system-prompt`, `--append-system-prompt` | via `--agent` | via `--agent` | via `AGENTS.md`, `-c` |
| tool allowlist | `--allowedTools`, `--tools` | via agent config | none | execpolicy via `-c` |
| session persistence off | `--no-session-persistence` | n/a | n/a | `--ephemeral` |
| reports cost | yes, `total_cost_usd` | yes, `cost` on `step_finish` | no (subscription) | no |

## Success payloads

**claude** emits one object with `type: "result"`:

```json
{"type":"result","subtype":"success","result":"OK","is_error":false,
 "total_cost_usd":0.0188,"duration_ms":1796,"session_id":"...","usage":{...}}
```

**agy** emits one object:

```json
{"conversation_id":"...","status":"SUCCESS","response":"OK\n",
 "duration_seconds":1.22,"num_turns":1,"usage":{"input_tokens":15110,...}}
```

Note `status` is a string to compare against `"SUCCESS"`, and `response` carries a
trailing newline.

**codex** emits JSONL. The answer arrives as an `item.completed` whose item type is
`agent_message`; usage arrives on `turn.completed`:

```json
{"type":"thread.started","thread_id":"..."}
{"type":"turn.started"}
{"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"OK"}}
{"type":"turn.completed","usage":{"input_tokens":15643,"cached_input_tokens":3456,...}}
```

**opencode** emits a whitespace-separated stream of objects. Text arrives in `text`
events and must be concatenated; cost and tokens arrive on `step_finish`:

```json
{"type":"step_start","part":{...}}
{"type":"text","part":{"type":"text","text":"OK"}}
{"type":"step_finish","part":{"reason":"stop","tokens":{...},"cost":1.29993}}
```

## Failure payloads

**claude** sets `is_error: true` on the same result object.

**agy** sets `status` to something other than `SUCCESS`.

**codex** emits `error` and `turn.failed` events and exits 1. The `-o` file is
**not created**, so its absence means failure, never an empty answer.

**opencode** emits a single object and exits 1:

```json
{"type":"error","sessionID":"...","error":{"name":"APIError",
 "data":{"message":"...","statusCode":401,"isRetryable":false}}}
```

This is why a parser must handle both one object and a stream. A naive
`json.loads()` on the whole of stdout works on every opencode failure and breaks
on every opencode success.
