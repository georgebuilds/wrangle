#!/usr/bin/env python3
"""wrangle - drive claude, opencode, agy and codex headlessly behind one interface.

Portable by design: this file ships no account-specific facts. Everything about
*your* machine (which CLIs exist, which providers you are authed to, which models
your plan actually serves, which route you prefer) is discovered at runtime and
cached under ~/.config/wrangle/.

Exit codes:
  0  success
  1  the agent ran and failed (model error, non-zero CLI exit)
  2  usage error (bad arguments)
  3  no route (unknown model, or no available route serves it)
  4  prerequisite missing (CLI not installed, provider not authed)
  5  timeout
  6  internal error
"""

from __future__ import annotations

import argparse
import itertools
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

EX_OK, EX_RUNFAIL, EX_USAGE, EX_NOROUTE, EX_PREREQ, EX_TIMEOUT, EX_INTERNAL = 0, 1, 2, 3, 4, 5, 6

STATE_DIR = Path(os.environ.get("WRANGLE_HOME", Path.home() / ".config" / "wrangle"))
PREFS_PATH = STATE_DIR / "prefs.json"
PROBE_PATH = STATE_DIR / "probes.json"
AGY_CACHE = STATE_DIR / "agy-models.json"
RUNS_DIR = STATE_DIR / "runs"

MODELS_DEV_CACHE = Path.home() / ".cache" / "opencode" / "models.json"
OPENCODE_AUTH = Path.home() / ".local" / "share" / "opencode" / "auth.json"
CODEX_AUTH = Path.home() / ".codex" / "auth.json"
AGY_CREDS = Path.home() / ".gemini" / "oauth_creds.json"

# A cached plan-entitlement verdict goes stale when someone upgrades their plan,
# so a rejection expires rather than hiding a route forever.
PROBE_TTL_S = 14 * 24 * 3600
AGY_CACHE_TTL_S = 7 * 24 * 3600
KEEP_RUN_LOGS = 200

RUNG_SUBSCRIPTION, RUNG_DIRECT, RUNG_GATEWAY = "subscription", "direct", "gateway"
RUNG_ORDER = {RUNG_SUBSCRIPTION: 0, RUNG_DIRECT: 1, RUNG_GATEWAY: 2}

# Providers reached with a first-party key rather than a reseller gateway.
DIRECT_PROVIDERS = {"openai", "anthropic", "google", "cerebras", "groq", "xai",
                    "mistral", "deepseek", "zhipuai"}

# Verified with `brew info`: antigravity is the IDE cask, antigravity-cli is the
# binary this drives; opencode is a core formula, not an sst tap.
BREW_INSTALL = {
    "claude": "brew install --cask claude-code",
    "codex": "brew install codex",
    "agy": "brew install antigravity-cli",
    "opencode": "brew install opencode",
}

AUTH_HINT = {
    "claude": "run `claude` then /login",
    "codex": "run `codex login`",
    "agy": "run `agy` once and sign in with Google",
    "opencode": "run `opencode auth login`",
}

# Each CLI accepts its own effort vocabulary. claude's is the widest, so it is the
# interface and everything else is clamped into range. agy hard-fails on an
# unknown value; codex forwards it to the API, which then fails.
EFFORT_SCALE = ["low", "medium", "high", "xhigh", "max"]
EFFORT_SUPPORT = {
    "claude": ["low", "medium", "high", "xhigh", "max"],
    "agy": ["low", "medium", "high"],
    "codex": ["low", "medium", "high"],
    "opencode": None,  # expressed as --variant, not --effort
}

# Each alias maps to a list of ALTERNATIVES (matched with OR). An alternative may
# be several words, which must all appear (AND). An alias names a *family*;
# variants stay visible as separate routes.
ALIASES = {
    "astra": ["gpt-6-astra", "gpt-astra"],
    "flash": ["gemini flash"],
    "gemini flash": ["gemini flash"],
    "gemini pro": ["gemini pro"],
    "opus": ["claude-opus"],
    "sonnet": ["claude-sonnet"],
    "haiku": ["claude-haiku"],
    "fable": ["claude-fable"],
    "glm": ["glm"],
    "kimi": ["kimi"],
    "qwen": ["qwen"],
    "grok": ["grok"],
}

_counter = itertools.count()


class Failure(Exception):
    def __init__(self, message: str, code: int = EX_INTERNAL):
        super().__init__(message)
        self.message = message
        self.code = code


# ---------------------------------------------------------------- small helpers

def warn(msg: str) -> None:
    print(f"wrangle: {msg}", file=sys.stderr)


def now() -> int:
    return int(time.time())


def read_json(path: Path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except FileNotFoundError:
        return default
    except (OSError, ValueError):
        return default


def write_json(path: Path, payload) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
        tmp.replace(path)
    except OSError as exc:
        warn(f"could not write {path}: {exc}")


def matches(hay: str, groups: list[list[str]]) -> bool:
    """True when any OR-group has all of its words present in hay."""
    hay = hay.lower()
    return any(group and all(word in hay for word in group) for group in groups)


def norm(text: str) -> str:
    return re.sub(r"[\s_]+", " ", (text or "").strip().lower())


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", norm(text)).strip("-")


def have(cli: str) -> bool:
    return shutil.which(cli) is not None


def clamp_effort(cli: str, effort: str | None) -> tuple[str | None, str | None]:
    """Map a requested effort onto what this CLI accepts.

    Returns (value, note). A tier the CLI lacks steps down to its ceiling rather
    than failing the run, and the note says so.
    """
    if not effort:
        return None, None
    supported = EFFORT_SUPPORT.get(cli)
    if supported is None:
        return None, f"{cli} has no effort flag; ignoring --effort {effort}"
    if effort in supported:
        return effort, None
    try:
        wanted = EFFORT_SCALE.index(effort)
    except ValueError:
        return None, f"unknown effort {effort!r}; ignoring"
    usable = [e for e in supported if EFFORT_SCALE.index(e) <= wanted]
    if not usable:
        return supported[0], f"{cli} has no {effort} tier; using {supported[0]}"
    best = max(usable, key=EFFORT_SCALE.index)
    return best, f"{cli} has no {effort} tier; using {best}"


AGY_TIER_RE = re.compile(r"-(high|medium|low)$")


def agy_model_for_effort(model: str, effort: str | None) -> tuple[str, str | None]:
    """agy encodes the reasoning tier in the model id and refuses --effort beside it.

    `--model gemini-3.8-flash-medium --effort high` is a hard error, so an effort
    request rewrites the suffix rather than adding a flag.
    """
    if not effort or not AGY_TIER_RE.search(model):
        return model, None
    current = AGY_TIER_RE.search(model).group(1)
    if current == effort:
        return model, None
    return AGY_TIER_RE.sub(f"-{effort}", model), f"agy tier set in the model id: {model} -> {AGY_TIER_RE.sub(f'-{effort}', model)}"


def codex_sandbox(opts: dict) -> str:
    """Deny writes by default, matching the other three.

    codex's own default is workspace-write with approval:never, which would edit
    the tree unprompted on a question that was only ever meant to be answered.
    """
    if opts.get("yolo"):
        return "danger-full-access"
    if opts.get("write"):
        return "workspace-write"
    return "read-only"


def decode_json_stream(raw: str):
    """Yield every JSON value in a string, skipping non-JSON noise.

    opencode emits ONE object on error but a whitespace-separated STREAM of them
    on success, so a plain json.loads() works right up until the run succeeds.
    codex can prefix stdout with a plain-text line, so a decoder that stops at the
    first bad byte would silently drop a whole successful run.
    """
    dec = json.JSONDecoder()
    idx, end = 0, len(raw)
    while idx < end:
        while idx < end and raw[idx] in " \t\r\n":
            idx += 1
        if idx >= end:
            return
        try:
            obj, idx = dec.raw_decode(raw, idx)
        except ValueError:
            nxt = raw.find("\n", idx)
            if nxt == -1:
                return
            idx = nxt + 1
            continue
        yield obj


def prune_runs() -> None:
    try:
        files = sorted(RUNS_DIR.glob("*.log"), key=lambda p: p.stat().st_mtime)
        for path in files[:-KEEP_RUN_LOGS]:
            try:
                path.unlink()
            except OSError:
                pass
    except OSError:
        pass


# ------------------------------------------------------------------- the catalog

class Catalog:
    """Model metadata from the models.dev cache opencode already maintains.

    Read-only and offline. If the cache is absent, routing still works; only the
    price and context columns go unknown.
    """

    def __init__(self) -> None:
        self.providers: dict[str, dict] = {}
        raw = read_json(MODELS_DEV_CACHE, default={}) or {}
        if isinstance(raw, dict):
            for pid, prov in raw.items():
                if isinstance(prov, dict) and isinstance(prov.get("models"), dict):
                    self.providers[pid] = prov

    @property
    def available(self) -> bool:
        return bool(self.providers)

    def lookup(self, provider: str, model_id: str) -> dict:
        prov = self.providers.get(provider) or {}
        return (prov.get("models") or {}).get(model_id) or {}

    def search(self, terms: list[list[str]]) -> list[tuple[str, str, dict]]:
        out = []
        for pid, prov in self.providers.items():
            for mid, meta in (prov.get("models") or {}).items():
                if matches(f"{pid}/{mid} {meta.get('name', '')}", terms):
                    out.append((pid, mid, meta))
        return out

    def providers_serving(self, terms: list[list[str]]) -> dict[str, list[str]]:
        found: dict[str, list[str]] = {}
        for pid, mid, _meta in self.search(terms):
            found.setdefault(pid, []).append(mid)
        return found


# ------------------------------------------------------------------ environment

class Environment:
    """What this machine can actually do, discovered fresh each invocation."""

    def __init__(self) -> None:
        self.clis = {name: have(name) for name in ("claude", "opencode", "agy", "codex")}
        self.opencode_providers = sorted((read_json(OPENCODE_AUTH, default={}) or {}).keys())
        codex_auth = read_json(CODEX_AUTH, default=None)
        self.codex_authed = codex_auth is not None
        self.codex_mode = "unknown"
        if isinstance(codex_auth, dict):
            self.codex_mode = "api-key" if codex_auth.get("OPENAI_API_KEY") else "chatgpt"
        self.agy_authed = AGY_CREDS.exists()

    def agy_models(self, refresh: bool = False) -> list[tuple[str, str]]:
        cached = read_json(AGY_CACHE, default=None)
        stale = None
        if isinstance(cached, dict):
            stale = [(m[0], m[1]) for m in cached.get("models", []) if len(m) == 2]
            if not refresh and now() - int(cached.get("fetched_at", 0)) < AGY_CACHE_TTL_S:
                return stale
        if not self.clis.get("agy"):
            return stale or []
        try:
            proc = subprocess.run(
                ["agy", "models"], capture_output=True, text=True, timeout=90,
                stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
            )
        except (OSError, subprocess.SubprocessError):
            return stale or []
        models = []
        for line in (proc.stdout or "").splitlines():
            if "\t" in line:
                mid, label = line.split("\t", 1)
                if mid.strip():
                    models.append((mid.strip(), label.strip()))
        if models:
            write_json(AGY_CACHE, {"fetched_at": now(), "models": [list(m) for m in models]})
            return models
        return stale or []


# ---------------------------------------------------------------------- routing

class Route:
    def __init__(self, cli, model, rung, provider=None, meta=None, variant=None, note=None):
        self.cli = cli
        self.model = model
        self.rung = rung
        self.provider = provider
        self.meta = meta or {}
        self.variant = variant
        self.note = note

    @property
    def ref(self) -> str:
        return f"{self.cli}:{self.model}"

    @property
    def cost_in(self):
        return (self.meta.get("cost") or {}).get("input")

    @property
    def cost_out(self):
        return (self.meta.get("cost") or {}).get("output")

    @property
    def context(self):
        return (self.meta.get("limit") or {}).get("context")

    def overhead_usd(self):
        """Cold-cache cost of the CLI's own tool surface, before your prompt.

        Only opencode carries a surface big enough to matter, and it cannot be
        trimmed away by config isolation. See reference/gotchas.md.
        """
        if self.cli != "opencode":
            return None
        rate = self.cost_in
        if rate is None:
            return None
        return round(104_000 / 1_000_000 * rate * 1.25, 4)

    def sort_key(self):
        # Within a tie, prefer the balanced effort tier over the extremes, the way
        # each vendor's own default does.
        effort_rank = {"medium": 0, "high": 1, "low": 2}
        suffix = (self.model or "").rsplit("-", 1)[-1]
        return (
            RUNG_ORDER.get(self.rung, 9),
            self.cost_in if self.cost_in is not None else 1e9,
            effort_rank.get(suffix, 0),
            -(self.context or 0),
        )

    def to_dict(self) -> dict:
        return {
            "ref": self.ref, "cli": self.cli, "model": self.model, "rung": self.rung,
            "provider": self.provider, "variant": self.variant,
            "cost_input_per_mtok": self.cost_in, "cost_output_per_mtok": self.cost_out,
            "context": self.context, "overhead_usd": self.overhead_usd(), "note": self.note,
        }


class Router:
    def __init__(self, env: Environment, catalog: Catalog) -> None:
        self.env = env
        self.catalog = catalog
        self.prefs = read_json(PREFS_PATH, default={}) or {}
        self.probes = read_json(PROBE_PATH, default={}) or {}
        self.hidden: list[str] = []

    # -- probes --------------------------------------------------------------
    def probe_verdict(self, cli: str, model: str) -> str | None:
        entry = (self.probes.get(cli) or {}).get(model)
        if not isinstance(entry, dict):
            return None
        if now() - int(entry.get("at", 0)) > PROBE_TTL_S:
            return None
        return entry.get("verdict")

    def record_probe(self, cli: str, model: str, verdict: str) -> None:
        self.probes.setdefault(cli, {})[model] = {"verdict": verdict, "at": now()}

    # -- query expansion -----------------------------------------------------
    def terms_for(self, query: str) -> list[list[str]]:
        """Return OR-groups; a route matches if ANY group fully matches."""
        q = norm(query)
        for alias, alts in ALIASES.items():
            if q == norm(alias):
                return [[w for w in norm(a).split(" ") if w] for a in alts]
        return [[w for w in q.split(" ") if w]]

    # -- candidate discovery -------------------------------------------------
    def routes_for(self, query: str) -> list[Route]:
        terms = self.terms_for(query)
        self.hidden = []
        routes: list[Route] = []
        routes += self._agy_routes(terms)
        routes += self._claude_routes(terms)
        routes += self._codex_routes(terms)
        routes += self._opencode_routes(terms)
        routes.sort(key=lambda r: r.sort_key())
        return routes

    def _agy_routes(self, terms) -> list[Route]:
        if not (self.env.clis.get("agy") and self.env.agy_authed):
            return []
        out = []
        for mid, label in self.env.agy_models():
            if not matches(f"{mid} {label}", terms):
                continue
            base = re.sub(r"-(high|medium|low)$", "", mid)
            meta = {}
            for pid in ("google", "opencode", "openrouter"):
                meta = self.catalog.lookup(pid, base) or self.catalog.lookup(pid, f"google/{base}")
                if meta:
                    break
            out.append(Route("agy", mid, RUNG_SUBSCRIPTION, provider="google",
                             meta=meta, note="included with your Google login"))
        return out

    def _claude_routes(self, terms) -> list[Route]:
        if not self.env.clis.get("claude"):
            return []
        out = []
        for mid, meta in (self.catalog.providers.get("anthropic", {}).get("models") or {}).items():
            if matches(f"{mid} {meta.get('name', '')}", terms):
                out.append(Route("claude", mid, RUNG_SUBSCRIPTION, provider="anthropic",
                                 meta=meta, note="included with your Claude plan"))
        if not out:
            for alias in ("opus", "sonnet", "haiku", "fable"):
                if matches(alias, terms) or matches(f"claude-{alias}", terms):
                    out.append(Route("claude", alias, RUNG_SUBSCRIPTION, provider="anthropic",
                                     note="model alias resolved by claude itself"))
        return out

    def _codex_routes(self, terms) -> list[Route]:
        if not (self.env.clis.get("codex") and self.env.codex_authed):
            return []
        subscription = self.env.codex_mode == "chatgpt"
        out = []
        for mid, meta in (self.catalog.providers.get("openai", {}).get("models") or {}).items():
            if not matches(f"{mid} {meta.get('name', '')}", terms):
                continue
            verdict = self.probe_verdict("codex", mid)
            if verdict == "rejected":
                self.hidden.append(f"codex:{mid}")
                continue
            if not subscription:
                note = "billed to your OpenAI API key"
            elif verdict == "ok":
                note = "included with your ChatGPT plan"
            else:
                note = "plan entitlement unverified; run `wrangle doctor --probe`"
            out.append(Route("codex", mid, RUNG_SUBSCRIPTION if subscription else RUNG_DIRECT,
                             provider="openai", meta=meta, note=note))
        return out

    def _opencode_routes(self, terms) -> list[Route]:
        if not self.env.clis.get("opencode"):
            return []
        out = []
        for pid in self.env.opencode_providers:
            prov = self.catalog.providers.get(pid)
            if not prov:
                continue
            for mid, meta in (prov.get("models") or {}).items():
                if not matches(f"{pid}/{mid} {meta.get('name', '')}", terms):
                    continue
                note = None
                if "gemini" in mid.lower():
                    note = "Gemini rejects opencode's tool schema; prefer agy"
                out.append(Route("opencode", f"{pid}/{mid}",
                                 RUNG_DIRECT if pid in DIRECT_PROVIDERS else RUNG_GATEWAY,
                                 provider=pid, meta=meta, note=note))
        return out

    # -- choosing ------------------------------------------------------------
    def preferred(self, query: str) -> str | None:
        return self.prefs.get("routes", {}).get(slug(query))

    def remember(self, query: str, ref: str) -> None:
        self.prefs.setdefault("routes", {})[slug(query)] = ref
        write_json(PREFS_PATH, self.prefs)

    def pick(self, query: str, routes: list[Route], explicit=None, policy=None) -> Route:
        if not routes:
            extra = f" ({len(self.hidden)} hidden by cached probe rejections)" if self.hidden else ""
            raise Failure(
                f"no available route serves {query!r}{extra}. Try `wrangle models {query}` "
                f"to see who could, or `wrangle doctor` for what is missing.", EX_NOROUTE)
        if explicit:
            for route in routes:
                if route.ref == explicit or route.model == explicit or route.cli == explicit:
                    return route
            raise Failure(f"route {explicit!r} does not serve {query!r}", EX_NOROUTE)
        remembered = self.preferred(query)
        if remembered:
            for route in routes:
                if route.ref == remembered:
                    return route
            warn(f"remembered route {remembered!r} no longer serves {query!r}; using the ranking")
        if policy == "fastest":
            return sorted(routes, key=lambda r: (0 if r.cli == "agy" else 1, r.sort_key()))[0]
        return routes[0]


# ---------------------------------------------------------------------- runners

class Result:
    def __init__(self, **kw):
        self.data = {
            "ok": False, "cli": None, "model": None, "route": None, "rung": None,
            "text": "", "exit_code": None, "duration_s": None, "cost_usd": None,
            "usage": None, "session_id": None, "error": None, "raw_path": None,
            "argv": [], "notes": [],
        }
        self.data.update(kw)

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v

    def note(self, msg: str | None) -> None:
        if msg:
            self.data["notes"].append(msg)


def launch(argv: list[str], cwd: str | None, timeout: int):
    """Run argv with a real timeout and no inherited stdin.

    macOS has no `timeout(1)`, so the deadline is enforced here. stdin is closed
    because codex reads it even when a prompt argument is present. Decoding
    replaces undecodable bytes rather than raising part-way through a run.
    """
    started = time.time()
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", start_new_session=True,
        )
    except FileNotFoundError:
        raise Failure(f"{argv[0]} is not installed. {BREW_INSTALL.get(argv[0], '')}".strip(), EX_PREREQ)
    except OSError as exc:
        raise Failure(f"could not start {argv[0]}: {exc}", EX_INTERNAL)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            time.sleep(1.5)
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        raise Failure(f"timed out after {timeout}s", EX_TIMEOUT)
    return proc.returncode, out or "", err or "", round(time.time() - started, 3)


def save_raw(tag: str, stdout: str, stderr: str) -> str | None:
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
        path = RUNS_DIR / f"{now()}-{os.getpid()}-{next(_counter)}-{tag}.log"
        path.write_text(f"--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}\n", encoding="utf-8")
        prune_runs()
        return str(path)
    except OSError:
        return None


def run_claude(route, prompt, cwd, timeout, opts):
    argv = ["claude", "-p", prompt, "--model", route.model, "--output-format", "json"]
    if opts.get("read_only"):
        argv += ["--permission-mode", "plan"]
    elif opts.get("yolo"):
        argv += ["--dangerously-skip-permissions"]
    elif opts.get("write"):
        argv += ["--permission-mode", "acceptEdits"]
    if opts.get("schema"):
        argv += ["--json-schema", opts["schema"]]
    if opts.get("budget"):
        argv += ["--max-budget-usd", str(opts["budget"])]
    effort, note = clamp_effort("claude", opts.get("effort"))
    if effort:
        argv += ["--effort", effort]
    if opts.get("agent"):
        argv += ["--agent", opts["agent"]]
    code, out, err, dur = launch(argv, cwd, timeout)
    res = Result(cli="claude", model=route.model, exit_code=code, duration_s=dur, argv=argv)
    res.note(note)
    doc = None
    for obj in decode_json_stream(out):
        if isinstance(obj, dict) and obj.get("type") == "result":
            doc = obj
    if doc:
        res["text"] = doc.get("result") or ""
        res["ok"] = (code == 0) and not doc.get("is_error")
        res["cost_usd"] = doc.get("total_cost_usd")
        res["usage"] = doc.get("usage")
        res["session_id"] = doc.get("session_id")
        if doc.get("is_error"):
            res["error"] = doc.get("subtype") or "claude reported is_error"
    else:
        res["error"] = (err or out or "claude produced no parsable result").strip()[:600]
    return res, out, err


def run_agy(route, prompt, cwd, timeout, opts):
    # --print-timeout defaults to 5m and silently truncates; always set it.
    effort, note = clamp_effort("agy", opts.get("effort"))
    model, retier = agy_model_for_effort(route.model, effort)
    note = retier or note
    argv = ["agy", "-p", prompt, "--model", model,
            "--output-format", "json", "--print-timeout", f"{timeout}s"]
    # agy's read-only analog is `--mode plan`. `--sandbox` restricts the terminal,
    # which is a different and weaker promise.
    if opts.get("read_only"):
        argv += ["--mode", "plan"]
    elif opts.get("yolo"):
        argv += ["--dangerously-skip-permissions"]
    elif opts.get("write"):
        argv += ["--mode", "accept-edits"]
    if opts.get("schema"):
        argv += ["--json-schema", opts["schema"]]
    if opts.get("agent"):
        argv += ["--agent", opts["agent"]]
    if opts.get("add_dir"):
        argv += ["--add-dir", opts["add_dir"]]
    if effort and not AGY_TIER_RE.search(model):
        argv += ["--effort", effort]
    code, out, err, dur = launch(argv, cwd, timeout + 15)
    res = Result(cli="agy", model=model, exit_code=code, duration_s=dur, argv=argv)
    res.note(note)
    doc = next((o for o in decode_json_stream(out) if isinstance(o, dict) and "status" in o), None)
    if doc:
        res["text"] = (doc.get("response") or "").strip()
        res["ok"] = (code == 0) and str(doc.get("status", "")).upper() == "SUCCESS"
        res["usage"] = doc.get("usage")
        res["session_id"] = doc.get("conversation_id")
        if not res["ok"]:
            res["error"] = doc.get("error") or f"agy status={doc.get('status')}"
    else:
        res["error"] = (err or out or "agy produced no parsable result").strip()[:600]
    return res, out, err


def run_codex(route, prompt, cwd, timeout, opts):
    try:
        RUNS_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    # Unique per process and per call: two routes in one compare must not share
    # this file, or one run's answer leaks into the other's.
    last = RUNS_DIR / f"codex-last-{os.getpid()}-{next(_counter)}.txt"
    argv = ["codex", "exec", "-m", route.model, "-s", codex_sandbox(opts),
            "--skip-git-repo-check", "--ephemeral", "--json", "-o", str(last)]
    if opts.get("schema_file"):
        argv += ["--output-schema", opts["schema_file"]]
    effort, note = clamp_effort("codex", opts.get("effort"))
    if effort:
        argv += ["-c", f"model_reasoning_effort={effort}"]
    if cwd:
        argv += ["-C", cwd]
    argv.append(prompt)
    code, out, err, dur = launch(argv, cwd, timeout)
    res = Result(cli="codex", model=route.model, exit_code=code, duration_s=dur, argv=argv)
    res.note(note)
    text, usage, failure = "", None, None
    for obj in decode_json_stream(out):
        if not isinstance(obj, dict):
            continue
        kind = obj.get("type")
        if kind == "item.completed":
            item = obj.get("item") or {}
            if item.get("type") == "agent_message":
                text = item.get("text") or text
        elif kind == "turn.completed":
            usage = obj.get("usage")
        elif kind in ("error", "turn.failed"):
            raw = obj.get("message") or (obj.get("error") or {}).get("message") or ""
            failure = _codex_reason(raw) or failure
    # The -o file is never created on failure, so its absence is not "empty answer".
    if not text and last.exists():
        try:
            text = last.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            pass
    try:
        last.unlink(missing_ok=True)
    except OSError:
        pass
    res["text"] = text
    res["usage"] = usage
    res["ok"] = code == 0 and failure is None
    if not res["ok"]:
        res["error"] = failure or (err or "codex exited non-zero").strip()[:600]
    return res, out, err


def _codex_reason(raw: str) -> str | None:
    if not raw:
        return None
    try:
        doc = json.loads(raw)
        msg = ((doc.get("error") or {}).get("message")) or doc.get("message") or raw
    except ValueError:
        msg = raw
    if "not supported when using Codex with a ChatGPT account" in msg:
        return "plan-gap: your ChatGPT plan does not include this model"
    return msg.strip()[:400]


def run_opencode(route, prompt, cwd, timeout, opts):
    argv = ["opencode", "run", "-m", route.model, "--format", "json"]
    if opts.get("variant"):
        argv += ["--variant", opts["variant"]]
    # opencode ships a built-in read-only `plan` agent; an explicit --agent wins,
    # and the caller is told the flag did not take.
    agent, note = opts.get("agent"), None
    if opts.get("read_only"):
        if agent:
            note = f"--read-only not enforced: explicit --agent {agent} takes precedence"
        else:
            agent = "plan"
    if agent:
        argv += ["--agent", agent]
    if opts.get("yolo"):
        argv += ["--auto"]
    if cwd:
        argv += ["--dir", cwd]
    argv.append(prompt)
    code, out, err, dur = launch(argv, cwd, timeout)
    res = Result(cli="opencode", model=route.model, exit_code=code, duration_s=dur, argv=argv)
    res.note(note)
    res.note(clamp_effort("opencode", opts.get("effort"))[1])
    chunks, cost, tokens, failure, session = [], None, None, None, None
    for obj in decode_json_stream(out):
        if not isinstance(obj, dict):
            continue
        session = obj.get("sessionID") or session
        kind = obj.get("type")
        part = obj.get("part") or {}
        if kind == "text":
            chunks.append(part.get("text") or "")
        elif kind == "step_finish":
            cost = part.get("cost", cost)
            tokens = part.get("tokens", tokens)
        elif kind == "error":
            data = (obj.get("error") or {}).get("data") or {}
            failure = data.get("message") or (obj.get("error") or {}).get("name") or "opencode error"
    res["text"] = "".join(chunks).strip()
    res["cost_usd"] = cost
    res["usage"] = tokens
    res["session_id"] = session
    res["ok"] = code == 0 and failure is None
    if not res["ok"]:
        res["error"] = (failure or err or "opencode exited non-zero").strip()[:600]
    return res, out, err


RUNNERS = {"claude": run_claude, "agy": run_agy, "codex": run_codex, "opencode": run_opencode}


def execute(route: Route, prompt: str, cwd, timeout, opts) -> Result:
    runner = RUNNERS.get(route.cli)
    if not runner:
        raise Failure(f"no runner for {route.cli}", EX_INTERNAL)
    if not have(route.cli):
        raise Failure(f"{route.cli} is not installed. {BREW_INSTALL.get(route.cli, '')}".strip(), EX_PREREQ)
    res, out, err = runner(route, prompt, cwd, timeout, opts)
    res["route"] = route.ref
    res["rung"] = route.rung
    res["raw_path"] = save_raw(f"{route.cli}-{slug(route.model)}", out, err)
    return res


# --------------------------------------------------------------------- commands

def fmt_money(value):
    return "?" if value is None else f"${value:g}"


def cmd_resolve(args, router: Router):
    routes = router.routes_for(args.model)
    if args.json:
        print(json.dumps({
            "query": args.model, "preferred": router.preferred(args.model),
            "hidden_by_probe": router.hidden,
            "routes": [r.to_dict() for r in routes],
        }, indent=2))
        return EX_OK if routes else EX_NOROUTE
    if not routes:
        print(f"No available route serves {args.model!r}.\n")
        providers = router.catalog.providers_serving(router.terms_for(args.model))
        if providers:
            print("Providers that do serve it (you are not authed to these):")
            for pid in sorted(providers)[:12]:
                print(f"  {pid:<22} {', '.join(sorted(providers[pid])[:3])}")
            print("\nAdd one with `opencode auth login`, or see `wrangle doctor`.")
        else:
            print("Nothing in the catalog matches. Try a broader term.")
        if router.hidden:
            print(f"\n{len(router.hidden)} route(s) hidden by cached probe rejections. "
                  "Re-run `wrangle doctor --probe` after a plan change.")
        return EX_NOROUTE

    preferred = router.preferred(args.model)
    print(f"{len(routes)} route(s) for {args.model!r}"
          + (f"   [remembered: {preferred}]" if preferred else "") + "\n")
    shown = routes[: args.limit]
    width = max([len(r.ref) for r in shown] + [len("ROUTE")]) + 2
    head = f"{'#':<3}{'ROUTE':<{width}}{'RUNG':<14}{'IN/OUT $/Mtok (list)':<22}{'CONTEXT':<11}"
    print(head)
    print("-" * len(head))
    for idx, route in enumerate(shown, 1):
        price = "included" if route.rung == RUNG_SUBSCRIPTION and route.cost_in is None else \
            f"{fmt_money(route.cost_in)} / {fmt_money(route.cost_out)}"
        ctx = f"{route.context:,}" if route.context else "?"
        mark = "*" if route.ref == preferred else " "
        print(f"{mark}{idx:<2}{route.ref:<{width}}{route.rung:<14}{price:<22}{ctx:<11}")
        extras = [route.note] if route.note else []
        over = route.overhead_usd()
        if over:
            extras.append(f"~{fmt_money(over)} cold-start tool overhead")
        for line in extras:
            print(f"     > {line}")
    if len(routes) > args.limit:
        print(f"\n   ... {len(routes) - args.limit} more, use --limit to widen")
    if router.hidden:
        print(f"\n   {len(router.hidden)} route(s) hidden by cached probe rejections "
              "(re-probe after a plan change)")
    print(f"\nPick one:  wrangle run --model {args.model!r} --route <ROUTE> --prompt '...'")
    print(f"Remember:  wrangle prefs set {args.model!r} <ROUTE>")
    return EX_OK


def collect_opts(args) -> dict:
    return {
        "read_only": getattr(args, "read_only", False), "yolo": getattr(args, "yolo", False),
        "write": getattr(args, "write", False), "schema": getattr(args, "schema", None),
        "schema_file": getattr(args, "schema_file", None), "effort": getattr(args, "effort", None),
        "variant": getattr(args, "variant", None), "agent": getattr(args, "agent", None),
        "budget": getattr(args, "budget", None), "add_dir": getattr(args, "add_dir", None),
    }


def guard_permissions(args) -> None:
    picked = [n.replace("_", "-") for n in ("read_only", "write", "yolo") if getattr(args, n, False)]
    if len(picked) > 1:
        raise Failure(f"--{' and --'.join(picked)} are mutually exclusive", EX_USAGE)


def cmd_run(args, router: Router):
    guard_permissions(args)
    prompt = read_prompt(args)
    routes = router.routes_for(args.model)
    route = router.pick(args.model, routes, explicit=args.route, policy=args.pick)
    if args.dry_run:
        print(json.dumps({"chosen": route.to_dict(),
                          "alternatives": [r.to_dict() for r in routes if r.ref != route.ref][:8]},
                         indent=2))
        return EX_OK
    res = execute(route, prompt, args.cwd, args.timeout, collect_opts(args))
    if args.json:
        print(json.dumps(res.data, indent=2))
    else:
        if res["text"]:
            print(res["text"])
        for line in res["notes"]:
            warn(line)
        if not res["ok"]:
            warn(f"{route.ref} failed: {res['error']}")
        meta = [f"route={route.ref}", f"rung={route.rung}", f"{res['duration_s']}s"]
        if res["cost_usd"] is not None:
            meta.append(fmt_money(round(res["cost_usd"], 4)))
        warn(" | ".join(meta))
    if args.remember and res["ok"]:
        router.remember(args.model, route.ref)
    return EX_OK if res["ok"] else EX_RUNFAIL


def cmd_compare(args, router: Router):
    guard_permissions(args)
    prompt = read_prompt(args)
    queries = [q.strip() for q in args.models.split(",") if q.strip()]
    if len(queries) < 2:
        raise Failure("compare needs at least two --models, comma separated", EX_USAGE)
    chosen = []
    for query in queries:
        try:
            chosen.append((query, router.pick(query, router.routes_for(query), policy=args.pick)))
        except Failure as exc:
            warn(f"skipping {query!r}: {exc.message}")
    if not chosen:
        raise Failure("no comparable routes resolved", EX_NOROUTE)

    est = sum(r.overhead_usd() or 0 for _q, r in chosen)
    if est >= args.confirm_over and not args.yes:
        warn(f"estimated cold-start overhead across {len(chosen)} routes is "
             f"~{fmt_money(round(est, 2))}; re-run with --yes to proceed")
        return EX_USAGE

    opts = collect_opts(args)
    results = []

    def one(item):
        query, route = item
        try:
            return query, execute(route, prompt, args.cwd, args.timeout, opts).data
        except Failure as exc:
            return query, Result(cli=route.cli, model=route.model, route=route.ref,
                                 error=exc.message).data

    with ThreadPoolExecutor(max_workers=min(len(chosen), args.jobs)) as pool:
        for query, data in pool.map(one, chosen):
            results.append({"query": query, **data})

    if args.json:
        print(json.dumps({"prompt": prompt, "results": results}, indent=2))
        return EX_OK if any(r["ok"] for r in results) else EX_RUNFAIL
    for item in results:
        print(f"=== {item['query']}  ({item['route']}) ===")
        print(item["text"] or f"[no output] {item['error'] or ''}")
        bits = [f"{item['duration_s']}s"] if item["duration_s"] else []
        if item["cost_usd"] is not None:
            bits.append(fmt_money(round(item["cost_usd"], 4)))
        if bits:
            print(f"    ({', '.join(bits)})")
        print()
    return EX_OK if any(r["ok"] for r in results) else EX_RUNFAIL


def cmd_models(args, router: Router):
    if not router.catalog.available:
        raise Failure(
            "no model catalog found. It ships with opencode at "
            f"{MODELS_DEV_CACHE}. Install opencode ({BREW_INSTALL['opencode']}) "
            "and run it once.", EX_PREREQ)
    if not args.query:
        if args.json:
            print(json.dumps({"providers": len(router.catalog.providers)}, indent=2))
        else:
            print(f"{len(router.catalog.providers)} providers in catalog. "
                  "Give a term, e.g. `wrangle models astra`.")
        return EX_OK
    routes = router.routes_for(args.query)
    reachable = {r.provider for r in routes}
    providers = router.catalog.providers_serving(router.terms_for(args.query))
    rows = []
    for pid in sorted(providers):
        ids = sorted(providers[pid])
        cost = router.catalog.lookup(pid, ids[0]).get("cost") or {}
        rows.append({"provider": pid, "reachable": pid in reachable, "models": ids,
                     "cost_input_per_mtok": cost.get("input"),
                     "cost_output_per_mtok": cost.get("output")})
    if args.json:
        print(json.dumps({"query": args.query, "providers": rows}, indent=2))
        return EX_OK
    print(f"Providers serving {args.query!r} ({len(providers)} total; "
          f"{sum(1 for r in rows if r['reachable'])} reachable by you)\n")
    for row in rows:
        price = f"{fmt_money(row['cost_input_per_mtok'])}/{fmt_money(row['cost_output_per_mtok'])}"
        print(f" {'+' if row['reachable'] else ' '} {row['provider']:<24}{price:<16}"
              f"{', '.join(row['models'][:3])}")
    print("\n  + = you can reach it today")
    return EX_OK


def cmd_prefs(args, router: Router):
    routes = router.prefs.get("routes", {})
    if args.action == "list":
        if args.json:
            print(json.dumps(routes, indent=2))
        elif not routes:
            print("No remembered routes yet.")
        else:
            for key in sorted(routes):
                print(f"{key:<28} {routes[key]}")
        return EX_OK
    if args.action == "set":
        if not (args.key and args.value):
            raise Failure("usage: wrangle prefs set <model> <route>", EX_USAGE)
        # A typo'd route would silently fall back to the ranking at pick time,
        # which looks exactly like the preference working.
        available = {r.ref for r in router.routes_for(args.key)}
        if args.value not in available:
            raise Failure(f"{args.value!r} is not a route for {args.key!r}. "
                          f"Run `wrangle resolve {args.key}` to see valid refs.", EX_USAGE)
        router.remember(args.key, args.value)
        print(f"remembered {slug(args.key)} -> {args.value}")
        return EX_OK
    if args.action == "clear":
        if args.key:
            routes.pop(slug(args.key), None)
        else:
            router.prefs["routes"] = {}
        write_json(PREFS_PATH, router.prefs)
        print("cleared")
        return EX_OK
    raise Failure(f"unknown prefs action {args.action!r}", EX_USAGE)


def cmd_doctor(args, router: Router):
    env = router.env
    missing = [n for n in ("claude", "opencode", "agy", "codex") if not env.clis.get(n)]
    report = {
        "clis": {n: {"installed": bool(env.clis.get(n)), "install": BREW_INSTALL.get(n)}
                 for n in ("claude", "opencode", "agy", "codex")},
        "auth": {
            "claude": "binary present" if env.clis["claude"] else "not installed",
            "agy": "google login" if env.agy_authed else "not logged in",
            "codex": env.codex_mode if env.codex_authed else "not logged in",
            "opencode": env.opencode_providers,
        },
        "catalog": {
            "path": str(MODELS_DEV_CACHE), "available": router.catalog.available,
            "providers": len(router.catalog.providers),
            "models": sum(len(p.get("models") or {}) for p in router.catalog.providers.values()),
        },
        "probes": {},
    }

    if args.refresh_models and env.clis.get("agy"):
        report["agy_models_refreshed"] = len(env.agy_models(refresh=True))

    if args.probe and env.clis.get("codex") and env.codex_authed:
        for model in [m.strip() for m in args.probe_models.split(",") if m.strip()]:
            verdict = _probe_codex(model)
            router.record_probe("codex", model, verdict)
            report["probes"][model] = verdict
        write_json(PROBE_PATH, router.probes)

    if args.json:
        print(json.dumps(report, indent=2))
        return EX_OK

    print("CLIs")
    for name in ("claude", "opencode", "agy", "codex"):
        if env.clis.get(name):
            print(f"  [ok]      {name}")
        else:
            print(f"  [missing] {name:<10} {BREW_INSTALL.get(name, '')}")
    print("\nAuth")
    print(f"  claude    {'binary present (plan auth not introspectable)' if env.clis['claude'] else 'n/a'}")
    print(f"  agy       {'Google login found' if env.agy_authed else 'not logged in: ' + AUTH_HINT['agy']}")
    print(f"  codex     {('logged in (' + env.codex_mode + ')') if env.codex_authed else 'not logged in: ' + AUTH_HINT['codex']}")
    print(f"  opencode  {', '.join(env.opencode_providers) if env.opencode_providers else 'no providers: ' + AUTH_HINT['opencode']}")
    print("\nCatalog")
    if router.catalog.available:
        print(f"  [ok]      {report['catalog']['providers']} providers, "
              f"{report['catalog']['models']} models ({MODELS_DEV_CACHE})")
    else:
        print(f"  [missing] no models.dev cache at {MODELS_DEV_CACHE}; prices will read '?'")
    if "agy_models_refreshed" in report:
        print(f"\nRefreshed agy catalog: {report['agy_models_refreshed']} models")
    if report["probes"]:
        print("\nProbed codex plan entitlements")
        for model, verdict in report["probes"].items():
            print(f"  {model:<22}{verdict}")
    if missing:
        print(f"\n{len(missing)} CLI(s) missing. Install with the brew lines above.")
    return EX_OK


def _probe_codex(model: str) -> str:
    try:
        code, out, err, _dur = launch(
            ["codex", "exec", "-m", model, "-s", "read-only", "--skip-git-repo-check",
             "--ephemeral", "--json", "Reply with exactly: OK"], None, 180)
    except Failure:
        return "error"
    if "not supported when using Codex with a ChatGPT account" in (out + err):
        return "rejected"
    return "ok" if code == 0 else "error"


def read_prompt(args) -> str:
    if args.prompt_file:
        try:
            return Path(args.prompt_file).read_text(encoding="utf-8")
        except OSError as exc:
            raise Failure(f"cannot read prompt file: {exc}", EX_USAGE)
    if args.prompt:
        return args.prompt
    if not sys.stdin.isatty():
        data = sys.stdin.read()
        if data.strip():
            return data
    raise Failure("no prompt: pass --prompt, --prompt-file, or pipe on stdin", EX_USAGE)


# --------------------------------------------------------------------- selftest

def _verdict_at(age_s: int):
    router = Router.__new__(Router)
    router.probes = {"codex": {"m": {"verdict": "rejected", "at": now() - age_s}}}
    return Router.probe_verdict(router, "codex", "m")


def cmd_selftest(_args, router: Router) -> int:
    checks, failures = [], 0

    def check(name, cond, detail=""):
        nonlocal failures
        checks.append((name, bool(cond), detail))
        if not cond:
            failures += 1

    stream = list(decode_json_stream('{"a":1}\n{"b":2}  {"c":3}'))
    check("json stream decoder", stream == [{"a": 1}, {"b": 2}, {"c": 3}], str(stream))
    check("single json still parses", list(decode_json_stream('{"a":1}')) == [{"a": 1}])
    check("garbage does not raise", list(decode_json_stream("not json")) == [])
    noisy = list(decode_json_stream('Reading additional input from stdin...\n{"a":1}\n{"b":2}'))
    check("leading banner is skipped", noisy == [{"a": 1}, {"b": 2}], str(noisy))
    mid = list(decode_json_stream('{"a":1}\nwarning: blah\n{"b":2}'))
    check("mid-stream noise is skipped", mid == [{"a": 1}, {"b": 2}], str(mid))

    check("slug", slug("Gemini Flash") == "gemini-flash", slug("Gemini Flash"))
    check("alias is OR of alternatives",
          router.terms_for("astra") == [["gpt-6-astra"], ["gpt-astra"]], str(router.terms_for("astra")))
    check("multi-word query is AND",
          router.terms_for("gemini flash") == [["gemini", "flash"]], str(router.terms_for("gemini flash")))
    check("OR matcher hits either alternative",
          matches("openai/gpt-6-astra", [["gpt-6-astra"], ["gpt-astra"]]))
    check("AND matcher needs all words", not matches("gemini-3.1-pro", [["gemini", "flash"]]))

    order = sorted([RUNG_GATEWAY, RUNG_SUBSCRIPTION, RUNG_DIRECT], key=lambda r: RUNG_ORDER[r])
    check("rung order cheapest first", order == [RUNG_SUBSCRIPTION, RUNG_DIRECT, RUNG_GATEWAY], str(order))

    cheap = Route("agy", "m", RUNG_SUBSCRIPTION)
    dear = Route("opencode", "p/m", RUNG_GATEWAY, meta={"cost": {"input": 10}})
    check("subscription outranks gateway", sorted([dear, cheap], key=lambda r: r.sort_key())[0] is cheap)
    check("opencode overhead estimated", (dear.overhead_usd() or 0) > 1.0, str(dear.overhead_usd()))
    check("non-opencode has no overhead", cheap.overhead_usd() is None)

    check("codex plan gap detected", (_codex_reason(json.dumps({"error": {"message":
          "The 'x' model is not supported when using Codex with a ChatGPT account."}})) or "")
          .startswith("plan-gap:"))

    check("effort passes through when supported", clamp_effort("claude", "xhigh")[0] == "xhigh")
    check("effort steps down for agy", clamp_effort("agy", "xhigh")[0] == "high",
          str(clamp_effort("agy", "xhigh")))
    check("effort steps down for codex", clamp_effort("codex", "max")[0] == "high")
    check("effort dropped for opencode", clamp_effort("opencode", "high")[0] is None)
    check("effort clamp explains itself", "no xhigh tier" in (clamp_effort("agy", "xhigh")[1] or ""))
    check("absent effort stays absent", clamp_effort("agy", None) == (None, None))

    check("agy retiers the model id rather than passing --effort",
          agy_model_for_effort("gemini-3.8-flash-medium", "high")[0] == "gemini-3.8-flash-high",
          str(agy_model_for_effort("gemini-3.8-flash-medium", "high")))
    check("agy leaves a matching tier alone",
          agy_model_for_effort("gemini-3.8-flash-high", "high") == ("gemini-3.8-flash-high", None))
    check("agy leaves a tierless id alone",
          agy_model_for_effort("claude-sonnet-4-6", "high") == ("claude-sonnet-4-6", None))

    check("codex denies writes by default", codex_sandbox({}) == "read-only", codex_sandbox({}))
    check("codex --write opts in", codex_sandbox({"write": True}) == "workspace-write")
    check("codex --yolo is full access", codex_sandbox({"yolo": True}) == "danger-full-access")
    check("codex --read-only stays read-only", codex_sandbox({"read_only": True}) == "read-only")

    check("stale probe verdict expires", _verdict_at(PROBE_TTL_S + 10) is None)
    check("fresh probe verdict is honoured", _verdict_at(60) == "rejected")

    try:
        launch([sys.executable, "-c", "import time; time.sleep(30)"], None, 2)
        check("sleep is killed at deadline", False, "no Failure raised")
    except Failure as exc:
        check("sleep is killed at deadline", exc.code == EX_TIMEOUT, exc.message)

    code, out, _err, _d = launch(
        [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'\\xff\\xfe ok bytes')"], None, 20)
    check("undecodable bytes do not raise", code == 0 and "ok bytes" in out, repr(out)[:60])

    width = max(len(n) for n, _, _ in checks)
    for name, ok, detail in checks:
        print(f"  [{'pass' if ok else 'FAIL'}] {name:<{width}}  {detail if not ok else ''}".rstrip())
    print(f"\n{len(checks) - failures}/{len(checks)} passed")
    return EX_OK if failures == 0 else EX_INTERNAL


# ------------------------------------------------------------------------- main

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="wrangle", description="Drive claude, opencode, agy and codex headlessly.")
    sub = ap.add_subparsers(dest="cmd")

    def shared(p):
        p.add_argument("--prompt")
        p.add_argument("--prompt-file")
        p.add_argument("--cwd")
        p.add_argument("--timeout", type=int, default=900)
        p.add_argument("--read-only", action="store_true",
                       help="deny edits (claude/agy: plan mode, opencode: plan agent, codex: read-only)")
        p.add_argument("--write", action="store_true", help="allow edits in the working tree")
        p.add_argument("--yolo", action="store_true", help="skip every permission check")
        p.add_argument("--effort", choices=EFFORT_SCALE,
                       help="clamped per CLI; agy and codex cap at high, opencode uses --variant")
        p.add_argument("--agent")
        p.add_argument("--pick", choices=["cheapest", "fastest", "first"], default=None)
        p.add_argument("--json", action="store_true")

    p = sub.add_parser("resolve", help="show every route for a model, ranked")
    p.add_argument("model")
    p.add_argument("--limit", type=int, default=12)
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("run", help="run one prompt on one model")
    p.add_argument("--model", required=True)
    p.add_argument("--route", help="force a specific route (from `resolve`)")
    p.add_argument("--schema", help="JSON schema string (claude, agy)")
    p.add_argument("--schema-file", help="JSON schema file (codex)")
    p.add_argument("--variant", help="opencode reasoning variant")
    p.add_argument("--add-dir")
    p.add_argument("--budget", type=float, help="claude --max-budget-usd")
    p.add_argument("--remember", action="store_true", help="remember this route on success")
    p.add_argument("--dry-run", action="store_true", help="print the chosen route and exit")
    shared(p)

    p = sub.add_parser("compare", help="run one prompt across several models in parallel")
    p.add_argument("--models", required=True, help="comma separated")
    p.add_argument("--jobs", type=int, default=4)
    p.add_argument("--confirm-over", type=float, default=1.0,
                   help="require --yes when estimated overhead exceeds this")
    p.add_argument("--yes", action="store_true")
    shared(p)

    p = sub.add_parser("models", help="who serves a model, reachable or not")
    p.add_argument("query", nargs="?")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("prefs", help="remembered route choices")
    p.add_argument("action", choices=["list", "set", "clear"])
    p.add_argument("key", nargs="?")
    p.add_argument("value", nargs="?")
    p.add_argument("--json", action="store_true")

    p = sub.add_parser("doctor", help="what is installed, authed and missing")
    p.add_argument("--probe", action="store_true", help="probe codex plan entitlements")
    p.add_argument("--probe-models", default="gpt-5.5,gpt-5.4,gpt-6-astra")
    p.add_argument("--refresh-models", action="store_true", help="refetch the agy model catalog")
    p.add_argument("--json", action="store_true")

    sub.add_parser("selftest", help="verify the wrapper's own logic")
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if not args.cmd:
        ap.print_help()
        return EX_USAGE
    try:
        router = Router(Environment(), Catalog())
        handlers = {
            "resolve": cmd_resolve, "run": cmd_run, "compare": cmd_compare,
            "models": cmd_models, "prefs": cmd_prefs, "doctor": cmd_doctor,
            "selftest": cmd_selftest,
        }
        return handlers[args.cmd](args, router)
    except Failure as exc:
        warn(exc.message)
        return exc.code
    except KeyboardInterrupt:
        warn("interrupted")
        return EX_INTERNAL
    except BrokenPipeError:
        return EX_OK
    except Exception as exc:  # never show a traceback to a caller
        warn(f"internal error: {type(exc).__name__}: {exc}")
        return EX_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
