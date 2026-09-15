# agy (Antigravity)

Verified against `agy 1.2.3`. A Go binary, Claude-Code-shaped. This is the
subscription route for Gemini via a Google sign-in.

## Headless shape

```bash
agy -p "PROMPT" --model gemini-3.8-flash-medium \
    --output-format json --print-timeout 900s
```

## Output

One object. On failure it carries an `error` string far more useful than `status`,
so read it rather than reporting the bare status:

```json
{"conversation_id":"","status":"ERROR","response":"",
 "error":"invalid model selection (...)","duration_seconds":0}
```

On success:

```json
{"conversation_id":"...","status":"SUCCESS","response":"OK\n",
 "duration_seconds":1.22,"num_turns":1,
 "usage":{"input_tokens":15110,"output_tokens":1,"thinking_tokens":0,
          "cache_read_tokens":0,"total_tokens":15111}}
```

Compare `status` against `"SUCCESS"`. `response` has a trailing newline. There is
no cost field, because the run is covered by the Google login.

## Flags worth knowing

| Flag | Why |
|---|---|
| `--print-timeout 900s` | **always set it**, the default is 5m and truncates silently |
| `--output-format text\|json\|stream-json` | |
| `--json-schema` | structured output; for stream-json it applies to the final result only |
| `--effort low\|medium\|high` | but see the model-id note below |
| `--mode accept-edits\|plan` | execution mode |
| `--mode plan` | **the read-only analog**; `--sandbox` only restricts the terminal |
| `--dangerously-skip-permissions` | auto-approve tools |
| `--add-dir` | repeatable |
| `--agent`, `--disable-slash-commands` | |
| `-c` / `--continue`, `--conversation <id>` | resume |
| `--input-format stream-json` | requires `--output-format stream-json` |

## Effort is encoded twice

`agy models` returns ids with the tier baked in:

```
gemini-3.8-flash-high    Gemini 3.8 Flash (High)
gemini-3.8-flash-medium  Gemini 3.8 Flash (Medium)
gemini-3.8-flash-low     Gemini 3.8 Flash (Low)
```

There is also a separate `--effort` flag, and **passing both is a hard error**:

```
error: invalid model selection (--model "gemini-3.8-flash-medium" --effort "high"):
  --model gemini-3.8-flash-medium conflicts with --effort=high
```

So an effort change must rewrite the id suffix, not add a flag. `--effort` is only
safe on an id that carries no tier, such as `claude-sonnet-4-6`. `--effort xhigh`
is rejected outright; valid values are `low`, `medium`, `high`.

`wrangle` treats `-medium` as the default tier and retiers the id on request.

## Discovering models

`agy models` hits the network and takes a few seconds, so cache it. `wrangle`
caches to `~/.config/wrangle/agy-models.json`. The catalog is Google-first and
also carries some Claude and GPT-OSS entries.

## Why Gemini belongs here

opencode cannot reliably call Gemini because of a tool-schema conflict, and agy is
the subscription rung anyway. Cheapest and most reliable coincide.
See [gotchas](gotchas.md).
