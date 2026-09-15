# codex (OpenAI Codex CLI)

Verified against `codex-cli 0.146.0`.

## Headless shape

```bash
codex exec -m gpt-5.5 -s read-only --skip-git-repo-check --ephemeral \
     --json -o /tmp/last.txt "PROMPT" </dev/null
```

`exec` (alias `e`) is the non-interactive subcommand. There is also a
non-interactive `codex review`.

## Output

JSONL. The answer is an `item.completed` whose `item.type` is `agent_message`;
usage lands on `turn.completed`. Failures emit `error` and `turn.failed` and exit 1.

`-o` / `--output-last-message FILE` is the cleanest extraction path, but **the file
is only created on success**.

## Flags worth knowing

| Flag | Why |
|---|---|
| `-s read-only\|workspace-write\|danger-full-access` | **the clearest sandbox control of the four** |
| `--output-schema FILE` | structured output, from a file rather than a string |
| `-o FILE` | final message only |
| `--json` | JSONL events |
| `-C` / `--cd DIR`, `--add-dir` | working roots |
| `--skip-git-repo-check` | required outside a git repo |
| `--ephemeral` | do not persist the session |
| `--ignore-user-config` | skip `~/.codex/config.toml` (auth still applies) |
| `-c key=value` | override any config value, TOML-parsed |
| `-a` / `--ask-for-approval never` | |
| `-p` / `--profile` | layer a named config profile |

`--search` (web search) is a **top-level** flag and is not listed on `exec`. Reach
it with `-c` if you need it there.

## Model availability is an account fact

`codex` has no `models` subcommand. A model id you are not entitled to produces an
HTTP 400 naming your plan, and an id that does not exist produces only a warning
before failing at the API. Neither is knowable without calling.

Probe and cache per machine. Never hardcode an entitlement list into anything
shared, because plans differ between people and change when someone upgrades.
`wrangle doctor --probe` does this and stores results in `~/.config/wrangle/probes.json`.

## Auth

`~/.codex/auth.json` carries either an `OPENAI_API_KEY` or ChatGPT OAuth tokens.
With OAuth, runs are on the subscription rung. With an API key they are billed
per token, and the entitlement restrictions do not apply the same way.
