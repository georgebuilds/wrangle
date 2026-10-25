#!/usr/bin/env python3
"""Find and read past threads of the other coding harnesses on this machine.

Reads the local stores of Codex, Claude Code, agy (Antigravity CLI and IDE) and
opencode, read-only, and prints JSON. Nothing here resumes, edits or deletes a
thread; `resume` only prints the native command for one.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timedelta, timezone
import glob
import json
import os
from pathlib import Path
import re
import sqlite3
import sys


HARNESSES = ("codex", "claude", "agy", "opencode")
MAX_INLINE_BYTES = 4096

# A family word to the harness whose roster carries it. Matched against the
# thread's recorded model as a substring as well.
FAMILIES = {
    "codex": ("gpt", "astra", "sol", "luna", "o3", "o4"),
    "claude": ("claude", "opus", "sonnet", "haiku", "fable"),
    "agy": ("gemini", "argon", "flash"),
}


def home() -> Path:
    return Path(os.environ.get("WRANGLE_THREADS_HOME") or Path.home())


def iso(ms: float | int | None) -> str | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(float(ms) / 1000, tz=timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return None


def parse_since(value: str | None) -> float | None:
    """`7d`, `36h`, `90m` or an ISO date, as a millisecond timestamp."""
    if not value:
        return None
    match = re.fullmatch(r"(\d+)([dhm])", value.strip())
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        delta = {"d": timedelta(days=amount), "h": timedelta(hours=amount), "m": timedelta(minutes=amount)}[unit]
        return (datetime.now(tz=timezone.utc) - delta).timestamp() * 1000
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp() * 1000


def jsonl(path: Path):
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def text_of(content) -> str:
    """Collapse a message's content, whatever shape the harness gave it."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for key in ("text", "input_text", "output_text"):
            if isinstance(content.get(key), str):
                return content[key]
        return ""
    if isinstance(content, list):
        return "\n".join(part for part in (text_of(item) for item in content) if part)
    return ""


def thread(harness, id, **fields) -> dict:
    row = dict(harness=harness, id=id, title=None, cwd=None, branch=None, model=None,
               started=None, updated=None, turns=None, subagent=False, path=None,
               fidelity="full", resume=None)
    row.update(fields)
    row["resume"] = row["resume"] or resume_command(harness, id)
    return row


def resume_command(harness: str, id: str) -> dict:
    """The native way back into a thread, interactive and headless."""
    if harness == "codex":
        return {"interactive": f"codex resume {id}",
                "headless": f'codex exec fork --json {id} "<prompt>"',
                "wrangle": f"wrangle.py codex --resume {id} --prompt '<prompt>'"}
    if harness == "claude":
        return {"interactive": f"claude --resume {id}",
                "headless": f'claude -p --resume {id} --fork-session "<prompt>"',
                "wrangle": f"wrangle.py claude --resume {id} --prompt '<prompt>'"}
    if harness == "agy":
        return {"interactive": f"agy --conversation {id}",
                "headless": f'agy --conversation {id} -p "<prompt>" --output-format json',
                "wrangle": f"wrangle.py agy --resume {id} --prompt '<prompt>'"}
    return {"interactive": f"opencode --session {id}", "headless": None, "wrangle": None}


# --- Codex ------------------------------------------------------------------

def codex_root() -> Path:
    return Path(os.environ.get("CODEX_HOME") or home() / ".codex")


def codex_state_db() -> Path | None:
    candidates = sorted(codex_root().glob("state_*.sqlite"), key=lambda p: p.stat().st_mtime)
    return candidates[-1] if candidates else None


def codex_threads(include_subagents: bool):
    db = codex_state_db()
    if db is not None:
        try:
            with closing(read_only(db)) as conn:
                rows = conn.execute(
                    "SELECT id, name, title, first_user_message, model, cwd, git_branch, rollout_path, "
                    "created_at_ms, updated_at_ms, recency_at_ms, archived, thread_source, source FROM threads"
                ).fetchall()
        except sqlite3.Error:
            rows = None
        if rows is not None:
            for row in rows:
                subagent = (row["thread_source"] or "") == "subagent" or (row["source"] or "").startswith("{")
                if subagent and not include_subagents:
                    continue
                yield thread(
                    "codex", row["id"], title=row["name"] or row["title"] or None,
                    first_prompt=row["first_user_message"] or None, cwd=row["cwd"], branch=row["git_branch"],
                    model=row["model"], started=iso(row["created_at_ms"]),
                    updated=iso(row["recency_at_ms"] or row["updated_at_ms"]),
                    subagent=subagent, archived=bool(row["archived"]), path=row["rollout_path"],
                    sort_ms=row["recency_at_ms"] or row["updated_at_ms"] or 0,
                )
            return
    for path in codex_root().glob("sessions/**/*.jsonl"):
        meta = next((r for r in jsonl(path) if r.get("type") == "session_meta"), None)
        if not meta:
            continue
        payload = meta.get("payload", {})
        ms = path.stat().st_mtime * 1000
        yield thread("codex", payload.get("id") or path.stem, cwd=payload.get("cwd"),
                     started=payload.get("timestamp"), updated=iso(ms), path=str(path), sort_ms=ms)


def codex_messages(path: Path):
    """User and assistant turns from a rollout. Reasoning is encrypted and skipped."""
    for record in jsonl(path):
        kind, payload = record.get("type"), record.get("payload") or {}
        if kind == "event_msg" and payload.get("type") == "item_completed":
            item = payload.get("item") or {}
            if item.get("type") == "UserMessage":
                yield dict(role="user", text=text_of(item.get("content")), at=record.get("timestamp"))
            elif item.get("type") == "AgentMessage":
                yield dict(role="assistant", text=text_of(item.get("content")), at=record.get("timestamp"))
            elif item.get("type") == "Reasoning":
                summary = "\n".join(s for s in item.get("summary_text") or [] if isinstance(s, str))
                if summary:
                    yield dict(role="reasoning", text=summary, at=record.get("timestamp"))
        elif kind == "response_item" and payload.get("type") in ("custom_tool_call", "function_call"):
            name = payload.get("name") or ""
            arguments = payload.get("input") if "input" in payload else payload.get("arguments")
            yield dict(role="tool", text=f"{name}: {text_of(arguments) if not isinstance(arguments, str) else arguments}"[:400],
                       at=record.get("timestamp"))


# --- Claude Code --------------------------------------------------------------

def claude_root() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or home() / ".claude")


def claude_threads(include_subagents: bool):
    for path in claude_root().glob("projects/*/*.jsonl"):
        first = {}
        title = None
        model = None
        users = 0
        last = None
        sidechain_only = True
        for record in jsonl(path):
            kind = record.get("type")
            if kind == "ai-title":
                title = record.get("aiTitle") or title
            elif kind == "custom-title":
                title = record.get("customTitle") or title
            elif kind in ("user", "assistant"):
                if not record.get("isSidechain"):
                    sidechain_only = False
                if not first and record.get("cwd"):
                    first = record
                if kind == "user" and isinstance((record.get("message") or {}).get("content"), str):
                    users += 1
                if kind == "assistant":
                    model = (record.get("message") or {}).get("model") or model
                last = record.get("timestamp") or last
        if not first:
            continue
        if sidechain_only and not include_subagents:
            continue
        ms = path.stat().st_mtime * 1000
        yield thread("claude", path.stem, title=title, cwd=first.get("cwd"), branch=first.get("gitBranch"),
                     model=model, started=first.get("timestamp"), updated=last or iso(ms), turns=users,
                     subagent=sidechain_only, path=str(path), sort_ms=ms)


def claude_messages(path: Path, include_subagents: bool = False):
    for record in jsonl(path):
        kind = record.get("type")
        if kind not in ("user", "assistant") or (record.get("isSidechain") and not include_subagents):
            continue
        message = record.get("message") or {}
        content = message.get("content")
        if kind == "user":
            if isinstance(content, str):
                yield dict(role="user", text=content, at=record.get("timestamp"))
            else:
                for part in content or []:
                    if isinstance(part, dict) and part.get("type") == "text":
                        yield dict(role="user", text=part.get("text", ""), at=record.get("timestamp"))
            continue
        for part in content if isinstance(content, list) else []:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                yield dict(role="assistant", text=part.get("text", ""), at=record.get("timestamp"))
            elif part.get("type") == "thinking" and part.get("thinking"):
                yield dict(role="reasoning", text=part["thinking"], at=record.get("timestamp"))
            elif part.get("type") == "tool_use":
                yield dict(role="tool", text=f"{part.get('name')}: {json.dumps(part.get('input'))}"[:400],
                           at=record.get("timestamp"))


# --- agy / Antigravity ----------------------------------------------------------

def gemini_root() -> Path:
    return Path(os.environ.get("GEMINI_HOME") or home() / ".gemini")


def agy_prompts() -> dict[str, list[dict]]:
    """Typed prompts by conversation, the one part of a conversation stored in the clear."""
    grouped: dict[str, list[dict]] = {}
    for app in ("antigravity-cli", "antigravity"):
        for record in jsonl(gemini_root() / app / "history.jsonl"):
            cid = record.get("conversationId")
            if cid and record.get("display"):
                grouped.setdefault(cid, []).append(dict(role="user", text=record["display"], at=iso(record.get("timestamp"))))
    return grouped


def agy_threads(include_subagents: bool):
    prompts = agy_prompts()
    for app in ("antigravity-cli", "antigravity"):
        db = gemini_root() / app / "conversation_summaries.db"
        if not db.exists():
            continue
        try:
            with closing(read_only(db)) as conn:
                rows = conn.execute(
                    "SELECT conversation_id, title, preview, step_count, last_modified_time, last_user_input_time, "
                    "workspace_uris, agent_name, parent_conversation_id FROM conversation_summaries"
                ).fetchall()
        except sqlite3.Error:
            continue
        for row in rows:
            subagent = bool(row["parent_conversation_id"]) or bool(row["agent_name"])
            if subagent and not include_subagents:
                continue
            try:
                uris = json.loads(row["workspace_uris"] or "[]")
            except ValueError:
                uris = []
            cwd = next((u[len("file://"):] for u in uris if isinstance(u, str) and u.startswith("file://")), None)
            asked = prompts.get(row["conversation_id"], [])
            updated = row["last_modified_time"] or row["last_user_input_time"]
            try:
                sort_ms = datetime.fromisoformat(str(updated).replace(" ", "T")).timestamp() * 1000
            except ValueError:
                sort_ms = 0
            yield thread("agy", row["conversation_id"], title=row["title"] or None,
                         first_prompt=(asked[0]["text"] if asked else row["preview"] or None), cwd=cwd,
                         updated=updated, turns=len(asked) or None, subagent=subagent,
                         path=str(gemini_root() / app / "conversations" / f"{row['conversation_id']}.db"),
                         fidelity="prompts", store=app, sort_ms=sort_ms)
    for path in gemini_root().glob("tmp/*/chats/*.json"):
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        messages = saved.get("messages") or []
        first = next((text_of(m.get("content")) for m in messages if m.get("type") == "user"), None)
        updated = saved.get("lastUpdated")
        try:
            sort_ms = datetime.fromisoformat(str(updated).replace("Z", "+00:00")).timestamp() * 1000
        except (TypeError, ValueError):
            sort_ms = path.stat().st_mtime * 1000
        yield thread("agy", saved.get("sessionId") or path.stem, title=path.stem, first_prompt=first,
                     cwd=None, started=saved.get("startTime"), updated=updated,
                     turns=sum(1 for m in messages if m.get("type") == "user"), path=str(path),
                     fidelity="full", store="saved-chat", sort_ms=sort_ms,
                     resume={"interactive": f"agy (then /chat resume {path.stem.removeprefix('session-')})",
                             "headless": None, "wrangle": None})


def agy_messages(row: dict):
    if row.get("store") == "saved-chat":
        try:
            saved = json.loads(Path(row["path"]).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for message in saved.get("messages") or []:
            role = "user" if message.get("type") == "user" else "assistant"
            yield dict(role=role, text=text_of(message.get("content")), at=message.get("timestamp"))
        return
    for prompt in agy_prompts().get(row["id"], []):
        yield prompt


# --- opencode -----------------------------------------------------------------

def opencode_db() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME") or home() / ".local" / "share")
    return base / "opencode" / "opencode.db"


def opencode_threads(include_subagents: bool):
    db = opencode_db()
    if not db.exists():
        return
    try:
        with closing(read_only(db)) as conn:
            rows = conn.execute(
                "SELECT id, parent_id, directory, title, model, time_created, time_updated, time_archived FROM session"
            ).fetchall()
            counts = dict(conn.execute(
                "SELECT session_id, COUNT(*) FROM message WHERE data LIKE '%\"role\":\"user\"%' GROUP BY session_id"
            ).fetchall())
    except sqlite3.Error:
        return
    for row in rows:
        subagent = bool(row["parent_id"])
        if subagent and not include_subagents:
            continue
        yield thread("opencode", row["id"], title=row["title"] or None, cwd=row["directory"], model=row["model"],
                     started=iso(row["time_created"]), updated=iso(row["time_updated"]),
                     turns=counts.get(row["id"]), subagent=subagent, archived=row["time_archived"] is not None,
                     path=str(db), sort_ms=row["time_updated"] or 0)


def opencode_messages(id: str):
    db = opencode_db()
    try:
        with closing(read_only(db)) as conn:
            messages = conn.execute(
                "SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created, id", (id,)
            ).fetchall()
            parts = conn.execute(
                "SELECT message_id, data FROM part WHERE session_id = ? ORDER BY time_created, id", (id,)
            ).fetchall()
    except sqlite3.Error:
        return
    by_message: dict[str, list[dict]] = {}
    for part in parts:
        try:
            by_message.setdefault(part["message_id"], []).append(json.loads(part["data"]))
        except ValueError:
            continue
    for message in messages:
        try:
            data = json.loads(message["data"])
        except ValueError:
            continue
        role = data.get("role") or "assistant"
        at = iso((data.get("time") or {}).get("created"))
        for part in by_message.get(message["id"], []):
            kind = part.get("type")
            if kind == "text" and part.get("text"):
                yield dict(role=role, text=part["text"], at=at)
            elif kind == "reasoning" and part.get("text"):
                yield dict(role="reasoning", text=part["text"], at=at)
            elif kind == "tool":
                yield dict(role="tool", text=f"{part.get('tool')}: {json.dumps((part.get('state') or {}).get('input'))}"[:400], at=at)


# --- common ---------------------------------------------------------------------

SOURCES = {"codex": codex_threads, "claude": claude_threads, "agy": agy_threads, "opencode": opencode_threads}


def stores() -> list[dict]:
    rows = []
    codex_db = codex_state_db()
    rows.append(dict(harness="codex", path=str(codex_db or codex_root() / "sessions"), present=bool(codex_db) or (codex_root() / "sessions").exists(),
                     readable="full", note="rollout JSONL holds user, assistant and tool turns; reasoning is stored encrypted and is not read"))
    rows.append(dict(harness="claude", path=str(claude_root() / "projects"), present=(claude_root() / "projects").exists(),
                     readable="full", note="one JSONL per session under the project's slug; subagent turns are sidechains"))
    agy_present = any((gemini_root() / app / "conversation_summaries.db").exists() for app in ("antigravity-cli", "antigravity"))
    rows.append(dict(harness="agy", path=str(gemini_root()), present=agy_present, readable="prompts",
                     note="titles, previews and typed prompts are in the clear; conversation bodies are protobuf blobs "
                          "the CLI does not document, so they are not decoded. Saved chats under tmp/*/chats are full. "
                          "Resume natively to read one"))
    rows.append(dict(harness="opencode", path=str(opencode_db()), present=opencode_db().exists(), readable="full",
                     note="session, message and part tables"))
    return rows


def harness_filter(value: str | None) -> list[str]:
    if not value:
        return list(HARNESSES)
    chosen = []
    for token in re.split(r"[,\s]+", value.strip().lower()):
        if token in HARNESSES:
            chosen.append(token)
            continue
        family = next((h for h, words in FAMILIES.items() if token in words), None)
        if family is None:
            raise SystemExit(f"unknown harness or model family: {token}")
        chosen.append(family)
    return list(dict.fromkeys(chosen))


def cwd_matches(row: dict, wanted: Path | None) -> bool:
    if wanted is None:
        return True
    cwd = row.get("cwd")
    if not cwd:
        return False
    try:
        actual = Path(cwd).resolve()
    except OSError:
        actual = Path(cwd)
    return actual == wanted or wanted in actual.parents or actual in wanted.parents


def model_matches(row: dict, wanted: str | None) -> bool:
    if not wanted:
        return True
    needle = wanted.lower()
    model = (row.get("model") or "").lower()
    if needle in model:
        return True
    family = next((h for h, words in FAMILIES.items() if needle in words), None)
    return family is not None and row["harness"] == family and not model


def collect(args) -> list[dict]:
    wanted = None if args.any_cwd else Path(args.cwd).resolve()
    since = parse_since(args.since)
    rows = []
    for harness in harness_filter(args.harness):
        for row in SOURCES[harness](args.include_subagents):
            if row.get("archived") and not args.include_archived:
                continue
            if since and (row.get("sort_ms") or 0) < since:
                continue
            if not cwd_matches(row, wanted) or not model_matches(row, args.model):
                continue
            rows.append(row)
    rows.sort(key=lambda r: r.get("sort_ms") or 0, reverse=True)
    return rows


def messages_for(row: dict, include_subagents: bool = False):
    if row["harness"] == "codex":
        return codex_messages(Path(row["path"]))
    if row["harness"] == "claude":
        return claude_messages(Path(row["path"]), include_subagents)
    if row["harness"] == "agy":
        return agy_messages(row)
    return opencode_messages(row["id"])


def find(harness: str, id: str) -> dict | None:
    for row in SOURCES[harness](True):
        if row["id"] == id or row["id"].startswith(id):
            return row
    return None


def public(row: dict) -> dict:
    return {k: v for k, v in row.items() if k != "sort_ms"}


def render(row: dict, roles: set[str], tail: int | None) -> str:
    lines = [f"# {row['harness']} {row['id']}", f"title: {row.get('title') or ''}", f"cwd: {row.get('cwd') or ''}",
             f"model: {row.get('model') or ''}", f"updated: {row.get('updated') or ''}", f"fidelity: {row.get('fidelity')}"]
    if row.get("fidelity") == "prompts":
        lines.append("note: only the typed prompts are readable for this harness; resume it natively for the answers")
    turns = [m for m in messages_for(row) if m["role"] in roles and m.get("text")]
    if tail:
        turns = turns[-tail:]
    lines.append("")
    for turn in turns:
        lines.append(f"[{turn['role']}] {turn.get('at') or ''}")
        lines.append(turn["text"].rstrip())
        lines.append("")
    return "\n".join(lines)


def search(args) -> list[dict]:
    tokens = [t.lower() for t in args.query if t.strip()]
    if not tokens:
        raise SystemExit("search needs at least one word")
    hits = []
    for row in collect(args):
        haystack = " ".join(str(row.get(k) or "") for k in ("title", "first_prompt")).lower()
        body = ""
        if args.deep or row["harness"] in ("agy",):
            roles = {"user", "assistant", "reasoning"} if args.deep else {"user"}
            body = "\n".join(m["text"] for m in messages_for(row) if m["role"] in roles and m.get("text"))
        elif args.prompts:
            body = "\n".join(m["text"] for m in messages_for(row) if m["role"] == "user" and m.get("text"))
        corpus = f"{haystack}\n{body.lower()}"
        matched = [t for t in tokens if t in corpus]
        if len(matched) < (len(tokens) if args.all_words else 1):
            continue
        snippet = None
        first = min((corpus.find(t) for t in matched), default=-1)
        if first >= 0:
            start = max(0, first - 80)
            snippet = corpus[start:first + 160].replace("\n", " ").strip()
        hits.append(dict(public(row), score=len(matched), matched=matched, snippet=snippet))
    hits.sort(key=lambda r: (r["score"], r.get("updated") or ""), reverse=True)
    return hits


def emit(payload, out: Path | None, inline: bool) -> None:
    rendered = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False, indent=None)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(rendered + "\n", encoding="utf-8")
        print(json.dumps(dict(file=str(out), bytes=len(rendered.encode("utf-8")), lines=len(rendered.splitlines()))))
        return
    if not inline and len(rendered.encode("utf-8")) > MAX_INLINE_BYTES and isinstance(payload, str):
        import tempfile
        fd, name = tempfile.mkstemp(prefix="wrangle-thread-", suffix=".txt")
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
        print(json.dumps(dict(file=name, bytes=len(rendered.encode("utf-8")), lines=len(rendered.splitlines()))))
        return
    print(rendered)


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    def filters(p):
        p.add_argument("--harness", help="codex, claude, agy, opencode, or a model family word such as astra; comma-separated")
        p.add_argument("--cwd", default=os.getcwd(), help="only threads whose working directory is this one or inside it (default: here)")
        p.add_argument("--any-cwd", action="store_true", help="do not filter by working directory")
        p.add_argument("--model", help="substring of the recorded model, or a family word such as astra or opus")
        p.add_argument("--since", help="7d, 36h, 90m or an ISO date")
        p.add_argument("--limit", type=int, default=20)
        p.add_argument("--include-subagents", action="store_true")
        p.add_argument("--include-archived", action="store_true")

    sub.add_parser("stores", help="which harness stores exist here and how readable each is")
    listing = sub.add_parser("list", help="recent threads, newest first")
    filters(listing)
    searching = sub.add_parser("search", help="threads whose title or first prompt mention the words")
    filters(searching)
    searching.add_argument("query", nargs="+")
    searching.add_argument("--prompts", action="store_true", help="also read every typed prompt (slower)")
    searching.add_argument("--deep", action="store_true", help="also read assistant text and readable reasoning (slowest)")
    searching.add_argument("--all-words", action="store_true", help="require every word rather than any")
    showing = sub.add_parser("show", help="render one thread as text")
    showing.add_argument("harness", choices=HARNESSES)
    showing.add_argument("id", help="full id or a unique prefix")
    showing.add_argument("--roles", default="user,assistant", help="comma list of user, assistant, reasoning, tool")
    showing.add_argument("--tail", type=int, help="only the last N turns")
    showing.add_argument("--out", type=Path, help="write the rendering here instead of stdout")
    showing.add_argument("--inline", action="store_true", help="print even a large rendering to stdout")
    resuming = sub.add_parser("resume", help="print the native commands that continue a thread")
    resuming.add_argument("harness", choices=HARNESSES)
    resuming.add_argument("id")
    return ap


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    if args.command == "stores":
        print(json.dumps(stores(), ensure_ascii=False))
        return 0
    if args.command == "list":
        rows = [public(r) for r in collect(args)[: args.limit]]
        print(json.dumps(rows, ensure_ascii=False))
        return 0
    if args.command == "search":
        print(json.dumps(search(args)[: args.limit], ensure_ascii=False))
        return 0
    row = find(args.harness, args.id)
    if row is None:
        print(json.dumps(dict(ok=False, error=f"no {args.harness} thread matches {args.id}")))
        return 1
    if args.command == "resume":
        print(json.dumps(dict(public(row), resume=resume_command(args.harness, row["id"])), ensure_ascii=False))
        return 0
    roles = {r.strip() for r in args.roles.split(",") if r.strip()}
    emit(render(row, roles, args.tail), args.out, args.inline)
    return 0


if __name__ == "__main__":
    sys.exit(main())
