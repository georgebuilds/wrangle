#!/usr/bin/env python3
"""Run a native coding CLI with a deadline and a common result envelope."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time


MAX_INLINE_BYTES = 4096


class Failure(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


def launch(argv, cwd, timeout, input_data=None):
    """Close stdin after optional control input; terminate on timeout or interruption."""
    try:
        proc = subprocess.Popen(argv, cwd=cwd, stdin=subprocess.PIPE if input_data is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace",
                                start_new_session=True)
    except FileNotFoundError as exc:
        raise Failure(f"Cannot start {argv[0]}: {exc}", 4) from exc
    try:
        out, err = proc.communicate(input=input_data, timeout=timeout)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            time.sleep(0.2)
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            if proc.poll() is None:
                raise Failure("Could not terminate the child process group", 5) from exc
        proc.communicate()
        if isinstance(exc, KeyboardInterrupt):
            raise
        raise Failure(f"Timed out after {timeout}s", 5) from exc
    return proc.returncode, out, err


def objects(raw):
    """Accept JSON objects, JSONL and CLI diagnostics between records."""
    decoder = json.JSONDecoder()
    while raw.strip():
        raw = raw.lstrip()
        try:
            value, end = decoder.raw_decode(raw)
        except ValueError:
            _, _, raw = raw.partition("\n")
            continue
        if isinstance(value, dict):
            yield value
        raw = raw[end:]


def check_subscription(harness, cwd):
    if harness == "claude":
        overrides = [key for key in (
            "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
            "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
        ) if os.environ.get(key) not in (None, "", "0", "false")]
        if overrides:
            raise Failure("Subscription route has credential/provider overrides: "
                          + ", ".join(overrides) + ". Review them before running.", 4)
        code, out, _ = launch(["claude", "auth", "status"], cwd, 15)
        status = next(objects(out), {})
        if code or not status.get("loggedIn") or status.get("authMethod") != "claude.ai":
            raise Failure("Claude subscription login not confirmed. Run claude auth login.", 4)
    elif harness == "codex":
        code, out, err = launch(["codex", "login", "status"], cwd, 15)
        if code or "logged in using chatgpt" not in (out + err).lower():
            raise Failure("ChatGPT login not confirmed. Run codex login.", 4)


def command(args, prompt, last):
    if args.harness == "codex":
        argv = ["codex", "exec", "--json", "--ephemeral", "--skip-git-repo-check",
                "-c", 'model_provider="openai"', "-c", 'forced_login_method="chatgpt"',
                "-s", "workspace-write" if args.write else "read-only", "-o", str(last)]
        if args.model:
            argv += ["-m", args.model]
        if args.effort:
            argv += ["-c", f'model_reasoning_effort="{args.effort}"']
        if args.service_tier:
            argv += ["-c", f'service_tier="{args.service_tier}"']
        for override in args.config:
            argv += ["-c", override]
        return argv + [prompt]
    argv = [args.harness, "-p", prompt, "--output-format", "json"]
    if args.model:
        argv += ["--model", args.model]
    if args.harness == "claude":
        argv += ["--permission-mode", "acceptEdits" if args.write else "plan"]
    else:
        argv += ["--mode", "accept-edits" if args.write else "plan",
                 "--print-timeout", f"{args.timeout}s"]
    if args.effort:
        argv += ["--effort", args.effort]
    return argv


def parse_result(harness, code, out, err, last):
    records = list(objects(out))
    result = dict(ok=False, text="", usage=None, reported_cost_usd=None,
                  session_id=None, error=None)
    if harness == "codex":
        completed = False
        for row in records:
            kind = row.get("type")
            if kind == "thread.started":
                result["session_id"] = row.get("thread_id")
            elif kind == "item.completed" and row.get("item", {}).get("type") == "agent_message":
                result["text"] = row["item"].get("text", "")
            elif kind == "turn.completed":
                completed = True
                result["usage"] = row.get("usage")
            elif kind in ("error", "turn.failed"):
                result["error"] = row.get("message") or row.get("error") or kind
        if last.exists():
            result["text"] = last.read_text(encoding="utf-8").strip()
        result["ok"] = code == 0 and completed and not result["error"]
    else:
        docs = [r for r in records if (r.get("type") == "result" if harness == "claude" else "status" in r)]
        if docs:
            doc = docs[-1]
            result["text"] = doc.get("result" if harness == "claude" else "response") or ""
            success = (not doc.get("is_error") and doc.get("subtype", "success") == "success"
                       if harness == "claude" else doc.get("status") == "SUCCESS")
            result["ok"] = code == 0 and success
            result["usage"] = doc.get("usage")
            result["reported_cost_usd"] = doc.get("total_cost_usd")
            result["session_id"] = doc.get("session_id" if harness == "claude" else "conversation_id")
            if not success:
                result["error"] = doc.get("error") or doc.get("errors") or doc.get("subtype") or doc.get("status")
    if not result["ok"] and not result["error"]:
        result["error"] = (err or out or "No successful completion record")[-2000:]
    return result


def store_large_answer(result, inline=False, output_dir=None):
    """Keep large answers out of stdout while leaving short results unchanged."""
    answer = result.get("text") or ""
    rendered = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
    content = rendered.encode("utf-8")
    if inline or len(content) <= MAX_INLINE_BYTES:
        return result
    directory = output_dir.expanduser().resolve() if output_dir else Path(tempfile.gettempdir())
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="wrangle-answer-", suffix=".txt", dir=directory)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(content)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    result.pop("text", None)
    result.update(file=name, bytes=len(content), lines=len(rendered.splitlines()))
    return result


def parser():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("harness", choices=("claude", "agy", "codex"))
    ap.add_argument("--model", help="native model name; omit to use the harness default")
    ap.add_argument("--effort", help="native effort value; no aliasing or clamping")
    ap.add_argument("--service-tier", help="codex only: service_tier such as fast or ultrafast; not an effort")
    ap.add_argument("--config", action="append", default=[], metavar="KEY=VALUE",
                    help="codex only: a raw -c override, repeatable, for settings the runner does not name")
    prompt = ap.add_mutually_exclusive_group()
    prompt.add_argument("--prompt")
    prompt.add_argument("--prompt-file", type=Path)
    ap.add_argument("--file", "-f", action="append", type=Path, default=[])
    ap.add_argument("--cwd", type=Path, default=Path.cwd())
    ap.add_argument("--timeout", type=int, default=900)
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--read-only", action="store_true", help="default")
    ap.add_argument("--dry-run", action="store_true", help="print argv without auth checks or execution")
    ap.add_argument("--inline", action="store_true", help="print even large answers in the JSON result")
    ap.add_argument("--output-dir", type=Path, help="scratch directory for large answer files")
    ap.add_argument("--result-file", type=Path,
                    help="also write the result JSON here, for a run started in the background")
    return ap


def record(args, payload):
    """Print the result envelope and, when asked, keep a copy on disk."""
    rendered = json.dumps(payload)
    print(rendered)
    if args.result_file:
        target = args.result_file.expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered + "\n", encoding="utf-8")


def main(argv=None):
    ap = parser()
    args = ap.parse_args(argv)
    if args.timeout <= 0:
        ap.error("--timeout must be positive")
    if args.harness != "codex" and (args.service_tier or args.config):
        ap.error("--service-tier and --config are Codex settings; the other harnesses have no equivalent flag")
    started = time.monotonic()
    try:
        if args.prompt_file:
            prompt = args.prompt_file.expanduser().read_text(encoding="utf-8")
        elif args.prompt is not None:
            prompt = args.prompt
        else:
            prompt = sys.stdin.read() if not sys.stdin.isatty() else ""
        for path in args.file:
            prompt += f"\n\n--- File: {path} ---\n{path.expanduser().read_text(encoding='utf-8')}"
        if not prompt.strip():
            raise Failure("Provide --prompt, --prompt-file, --file, or stdin", 2)
        with tempfile.TemporaryDirectory(prefix="wrangle-") as tmp:
            last = Path(tmp) / "answer.txt"
            argv = command(args, prompt, last)
            if args.dry_run:
                print(json.dumps({"harness": args.harness, "cwd": str(args.cwd), "argv": argv}))
                return 0
            check_subscription(args.harness, args.cwd)
            code, out, err = launch(argv, args.cwd, args.timeout)
            result = parse_result(args.harness, code, out, err, last)
            result.update(harness=args.harness, model=args.model, service_tier=args.service_tier,
                          exit_code=code, duration_s=round(time.monotonic() - started, 2))
            store_large_answer(result, inline=args.inline, output_dir=args.output_dir)
            record(args, result)
            return 0 if result["ok"] else 1
    except (Failure, OSError, UnicodeError) as exc:
        code = exc.code if isinstance(exc, Failure) else 2
        record(args, {"ok": False, "harness": args.harness, "error": str(exc), "exit_code": code})
        return code
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
