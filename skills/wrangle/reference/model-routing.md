# Model routing

How a name a person says becomes a command that runs.

## 1. Expand the alias

An alias names a **family**, and expands to OR-alternatives. Each alternative may
be several words, which must all match.

```
"astra"        -> [["gpt-6-astra"], ["gpt-astra"]]     # either
"gemini flash" -> [["gemini", "flash"]]                # both words
```

Never collapse a family to a single pinned id. Doing so hides variants such as
`-pro`, `-fast` and `-lite`, which is exactly the information the person needs to
choose.

"Latest" is handled by ordering, not by pinning: matching routes sort so the
highest version appears first. When someone says "Gemini Flash" they mean the
newest Flash, and they should still be able to see the older ones.

## 2. Collect candidate routes

| Source | Yields | Rung |
|---|---|---|
| `agy models` | Gemini, some Claude and GPT-OSS | subscription |
| catalog `anthropic` provider + claude aliases | Claude models | subscription |
| catalog `openai` provider, minus probed rejections | GPT models | subscription with ChatGPT auth, direct with an API key |
| `opencode` authed providers | everything else | direct for first-party, gateway for resellers |

Only routes whose CLI is installed **and** authed are candidates. Everything else
belongs in the "you could install this" list, not the ranking.

## 3. Rank

Sort by rung, then input price, then effort tier (`medium` before `high` before
`low`, matching vendor defaults), then context descending.

Ranking is advice. The caller still chooses, because price frequently ties and the
tiebreakers are things only a human knows, such as whether this task needs a
million-token context or the `-pro` variant.

## 4. Choose

1. `--route` explicit
2. remembered preference in `~/.config/wrangle/prefs.json`
3. `--pick cheapest|fastest|first`
4. otherwise ask

`--pick fastest` prefers agy first, since a subscription-rung local-vendor route
avoids gateway latency.

## 5. Vendor affinity

When a model's own vendor has a CLI on a subscription rung, prefer it:

| Family | Prefer | Why |
|---|---|---|
| Gemini | `agy` | subscription, and opencode's tool schema breaks Gemini outright |
| Claude | `claude` | subscription, cleanest output contract, only hard cost ceiling |
| GPT | `codex` when entitled | subscription; falls back to opencode gateways |
| GLM, Kimi, Qwen, Grok, everything else | `opencode` | the only route that exists |

## 6. When nothing serves it

Exit 3 and list providers from the catalog that do, marking which are reachable.
Then give the Homebrew install line and the auth command. A person asking for a
model they cannot reach wants a path forward, not a refusal.
