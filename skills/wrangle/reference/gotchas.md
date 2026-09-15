# Gotchas

Each entry was reproduced on a real machine. Where a number appears, it was
measured. These are the reasons the wrapper exists.

## macOS has no `timeout(1)`

`timeout` is GNU coreutils and is absent from a stock Mac. Any wrapper that shells
out to `timeout 300 some-cli ...` fails with `command not found` and looks like the
agent crashed. `gtimeout` exists only after `brew install coreutils`, which you
cannot assume.

Enforce deadlines in your own process instead. `wrangle.py` uses
`subprocess.Popen(start_new_session=True)` and kills the whole process group with
SIGTERM then SIGKILL, because these CLIs spawn children that outlive a bare
`proc.kill()`.

## agy silently truncates at five minutes

`--print-timeout` defaults to `5m0s`. A longer run is cut off and you get a partial
or empty response with no obvious error. Always pass `--print-timeout` explicitly,
matched to your own deadline.

## codex reads stdin even when given a prompt argument

`codex exec "prompt"` still prints `Reading additional input from stdin...`. If
stdin is an open pipe that never closes, it can block. Always redirect
`</dev/null` for one-shot runs.

## codex accepts an unknown model and fails only at the API

A bad model id produces a warning, not a refusal:

```
warning: Model metadata for `gpt-6-astra` not found. Defaulting to fallback
metadata; this can degrade performance and cause issues.
```

The run then proceeds and fails at the network call. You cannot validate a model
id by whether codex starts.

## A codex plan gap looks like a model error

When your account is not entitled to a model, the failure is an HTTP 400:

```json
{"type":"error","status":400,"error":{"type":"invalid_request_error",
 "message":"The 'X' model is not supported when using Codex with a ChatGPT account."}}
```

That string means *your plan does not include this*, not *this model does not
exist*. Entitlements differ per account and per plan tier, and they change when
someone upgrades, so they must be probed and cached locally, never baked into a
shared skill. `wrangle doctor --probe` does this; rejections cost nothing.

## codex's `-o` file is absent, not empty, on failure

`--output-last-message FILE` is the cleanest way to get the final answer, but the
file is only created on success. Treat a missing file as failure. Reading it and
finding nothing must not be reported as "the model returned an empty answer".

## agy refuses --model and --effort together

The reasoning tier is encoded in the model id (`gemini-3.8-flash-medium`), and agy
also exposes `--effort`. Supplying both is a hard error, not a precedence rule:

```
error: invalid model selection (--model "gemini-3.8-flash-medium" --effort "high"):
  --model gemini-3.8-flash-medium conflicts with --effort=high
```

Change the tier by rewriting the id suffix. `--effort` is only safe on an id with
no tier. Valid values are `low`, `medium`, `high`, so a wider scale must be clamped
before it reaches agy.

Not every agy flag fails loudly, though: `--mode bogus` only warns and then runs
with the default. Validate before passing rather than trusting a non-zero exit.

## codex writes to your tree by default

`codex exec` defaults to `-s workspace-write` with `approval: never`, so a run meant
only to answer a question can edit files unprompted. The other three deny or prompt
by default. Pass `-s read-only` explicitly for anything read-only, which is what
`wrangle` does unless you ask for `--write`.

## opencode emits one object on error and a stream on success

See [capability-matrix](capability-matrix.md#failure-payloads). A parser built
against a failing run will break the first time the run works.

## opencode sends your entire MCP tool surface on every call

This is the expensive one. With eight MCP servers configured, opencode sent **352
tool declarations**, measured as **103,972 cache-write tokens**, on a prompt whose
actual content was three tokens.

On a model at $10/M input, `"Reply with exactly: OK"` cost **$1.30**.

Two consequences:

- **Fan-out multiplies it.** Comparing five models on one question pays that
  overhead five times, because each route is a separate cold cache. `wrangle
  compare` estimates the total and refuses above `--confirm-over` without `--yes`.
- **Repeat calls amortize it.** Within the cache TTL the same surface is a cache
  *read*, which is far cheaper. The headline cost is a cold-start cost.

Attempts that did **not** reduce it, all verified: `--pure`, `OPENCODE_CONFIG`,
`OPENCODE_CONFIG_DIR` with an empty `mcp` map, `OPENCODE_CONFIG_CONTENT`, and
`OPENCODE_DISABLE_PROJECT_CONFIG=1`. The error indices stayed byte-identical
across all of them, which is how we know the surface never shrank. The documented
lever that remains is `--agent` with a restricted `tools` map. Until that is
verified, prefer a different CLI when tool surface matters and treat the overhead
as a cost of using opencode.

## Gemini rejects opencode's tool schema outright

Because of the same surface, Gemini refuses the request during validation:

```
[Google AI Studio] * GenerateContentRequest.tools[0].function_declarations[347]
  .parameters.properties[Period].items: field predicate failed: $type == Type.ARRAY
```

Gemini validates function declarations more strictly than other vendors, and two
of the 352 declarations are malformed. The whole request dies. Other vendors
accept the same payload, which is why this reads as a Gemini problem but is not.

**Therefore: route Gemini through `agy`, not opencode.** agy is also the
subscription rung for Gemini, so the cheapest route and the working route are the
same one.

## `claude -p` is not free of context

A `claude -p` run in a project directory loads `CLAUDE.md`, skills and settings.
A trivial prompt measured 13,174 cache-creation tokens and $0.019 on Haiku. That is
correct behaviour and usually what you want, but it means a one-shot `claude -p` is
not a bare model call. `--bare` skips that machinery but restricts auth to
`ANTHROPIC_API_KEY`, so it breaks subscription auth.

## opencode's model namespace is large and overlapping

503 models across five authed providers on the test machine, 7,825 across 217
providers in the catalog. The same model is commonly reachable three or four ways
at different prices, and sometimes the *direct* vendor provider is the one that
cannot serve the newest version. Always resolve rather than assume.

## A plaintext credential can hide in a CLI config

`~/.config/opencode/opencode.jsonc` stores MCP server environment blocks inline,
including API tokens. Anything that prints or syncs that file leaks them. Worth an
audit before sharing a config or committing a dotfiles repo.
