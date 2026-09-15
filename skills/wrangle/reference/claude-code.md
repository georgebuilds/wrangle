# claude (Claude Code)

Verified against `claude 2.1.238`.

## Headless shape

```bash
claude -p "PROMPT" --model sonnet --output-format json
```

- `-p` / `--print` is the headless switch. Without it you get a TUI.
- `--model` accepts an alias (`opus`, `sonnet`, `haiku`, `fable`) or a full id
  (`claude-sonnet-5`). The alias always resolves to the latest of that line, which
  makes it the right choice when someone says "use Sonnet".
- `--output-format` is `text` (default), `json`, or `stream-json`.

## Output

One object, `type: "result"`. Read `result` for the text and `is_error` for
success. `total_cost_usd` is authoritative and worth recording.

## Flags worth knowing

| Flag | Why |
|---|---|
| `--effort low\|medium\|high\|xhigh\|max` | reasoning effort |
| `--json-schema '<schema>'` | structured output, validated |
| `--max-budget-usd N` | **the only hard cost ceiling of the four** |
| `--permission-mode plan` | read-only; `acceptEdits`, `bypassPermissions` also exist |
| `--tools "Bash,Read"` | restrict the toolset; `""` disables all tools |
| `--allowedTools` / `--disallowedTools` | finer permission control |
| `--system-prompt`, `--append-system-prompt` | steer without a file |
| `--add-dir` | extra readable/writable roots |
| `--agents '<json>'` | define subagents inline |
| `--no-session-persistence` | do not write a resumable session |
| `--fallback-model a,b` | automatic failover when overloaded (print mode only) |
| `--strict-mcp-config` + `--mcp-config` | run with only the MCP servers you name |

## Cost note

A `-p` run loads `CLAUDE.md`, skills and settings from the working directory. That
is context you are paying to cache. `--bare` skips it but forces `ANTHROPIC_API_KEY`
auth and will not use a subscription. See [gotchas](gotchas.md).

## Restricting the tool surface

Unlike opencode, claude gives you direct control: `--tools ""` for a pure model
call, or `--strict-mcp-config --mcp-config '{}'` to drop MCP servers. Use this when
you want a cheap one-shot answer rather than an agentic run.
