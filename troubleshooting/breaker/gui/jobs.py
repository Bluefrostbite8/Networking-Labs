"""Background jobs: every button that touches CML runs the same CLI command as a
subprocess (`python -m breaker ...`), so the GUI never grades differently from
the terminal.

Each job writes runs/gui-jobs/<id>.log (the command's own output) and <id>.json
(command, pid, start, end, exit code). Output goes straight to the log file, so
a job keeps running and logging even if the GUI server stops; the page just
reads the file again when it comes back.
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ..lab_model import TOOL_ROOT

JOBS_DIR = TOOL_ROOT / "runs" / "gui-jobs"
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Commands that drive the CML server: one at a time (node cap, shared consoles).
CML_COMMANDS = {"start", "grade", "selftest", "batch", "clean", "baseline", "rebuild", "doctor"}

_lock = threading.Lock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _meta_path(job_id: str) -> Path:
    return JOBS_DIR / f"{job_id}.json"


def _write(meta: dict[str, Any]) -> None:
    _meta_path(meta["id"]).write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def load(job_id: str) -> dict[str, Any] | None:
    path = _meta_path(job_id)
    if not path.exists():
        return None
    meta = json.loads(path.read_text(encoding="utf-8"))
    if meta.get("returncode") is None and not meta.get("ended"):
        meta["status"] = "running" if _alive(meta.get("pid")) else "ended"
    else:
        meta["status"] = "done" if meta.get("returncode") == 0 else "failed"
    return meta


def all_jobs(limit: int = 30) -> list[dict[str, Any]]:
    if not JOBS_DIR.exists():
        return []
    out = []
    for path in sorted(JOBS_DIR.glob("*.json"), reverse=True)[:limit]:
        meta = load(path.stem)
        if meta:
            out.append(meta)
    return out


def running_cml_job() -> dict[str, Any] | None:
    return next((j for j in all_jobs(50) if j["status"] == "running" and j["command"] in CML_COMMANDS), None)


def latest_for(target: str) -> dict[str, Any] | None:
    return next((j for j in all_jobs(100) if j.get("target") == target), None)


class Busy(RuntimeError):
    pass


def start(args: list[str], title: str, target: str | None = None) -> dict[str, Any]:
    """Run `python -m breaker <args>` in the background and return its job record."""
    command = args[0]
    with _lock:
        if command in CML_COMMANDS:
            busy = running_cml_job()
            if busy:
                raise Busy(f"{busy['title']} is still running; one CML job at a time.")
        JOBS_DIR.mkdir(parents=True, exist_ok=True)
        job_id = f"{datetime.now():%Y%m%d-%H%M%S}-{command}"
        log = JOBS_DIR / f"{job_id}.log"
        meta: dict[str, Any] = {"id": job_id, "command": command, "args": args, "title": title,
                                "target": target, "started": _now(), "pid": None, "returncode": None,
                                "ended": None, "log": log.name}
        fh = log.open("wb")
        env = {**os.environ, "PYTHONUNBUFFERED": "1"}
        proc = subprocess.Popen([sys.executable, "-m", "breaker", *args], cwd=TOOL_ROOT, stdout=fh,
                                stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, env=env,
                                start_new_session=True)
        fh.close()
        meta["pid"] = proc.pid
        _write(meta)

    def wait() -> None:
        code = proc.wait()
        meta.update(returncode=code, ended=_now())
        _write(meta)

    threading.Thread(target=wait, daemon=True).start()
    return load(job_id) or meta


def stop(job_id: str) -> bool:
    """Ctrl+C for the job: the CLI prints where it was and leaves the clean-up command."""
    meta = load(job_id)
    if not meta or meta["status"] != "running":
        return False
    try:
        os.killpg(meta["pid"], signal.SIGINT)
    except OSError:
        return False
    return True


def read(job_id: str, offset: int = 0) -> dict[str, Any]:
    meta = load(job_id)
    if not meta:
        return {"error": "no such job"}
    path = JOBS_DIR / meta["log"]
    data = b""
    if path.exists():
        with path.open("rb") as fh:
            fh.seek(offset)
            data = fh.read()
    text = ANSI.sub("", data.decode("utf-8", errors="replace"))
    return {"id": job_id, "status": meta["status"], "returncode": meta.get("returncode"),
            "started": meta["started"], "ended": meta.get("ended"), "title": meta["title"],
            "offset": offset + len(data), "text": text}


def run_now(args: list[str], timeout: int = 120) -> tuple[int, str]:
    """Quick local commands (break, hint, debrief): run and wait, return (code, output)."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    proc = subprocess.run([sys.executable, "-m", "breaker", *args], cwd=TOOL_ROOT, capture_output=True,
                          text=True, timeout=timeout, env=env, stdin=subprocess.DEVNULL)
    return proc.returncode, ANSI.sub("", (proc.stdout or "") + (proc.stderr or ""))
