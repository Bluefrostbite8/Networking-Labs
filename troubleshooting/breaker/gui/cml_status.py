"""Live CML status for the GUI: node usage against the licence cap and the labs
on the server. Read in a background thread and cached briefly, so a slow
controller never holds up a page; the page asks for it over /api/cml."""

from __future__ import annotations

import threading
import time
from typing import Any

from .. import cml_api
from ..lab_model import INFRA_DEFINITIONS

TTL_S = 20          # how long a reading counts as fresh
WAIT_S = 8          # how long a request waits for a fresh reading before answering with the last one

_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "data": None}
_worker: threading.Thread | None = None


def _kind(incident: str | None) -> str:
    if not incident:
        return "other"
    if "-st-" in incident:
        return "selftest"
    if "-healthy-" in incident:
        return "healthy"
    return "session"


def _read() -> dict[str, Any]:
    out: dict[str, Any] = {"ok": True, "cap": None, "cap_error": None, "labs": [], "error": None}
    try:
        out["cap"] = cml_api.load_config()["node_cap"]
    except cml_api.CmlError as exc:
        out["cap_error"] = str(exc)
    client = cml_api.connect()
    for lab in client.all_labs():
        try:
            lab.sync_states()
            nodes = [n for n in lab.nodes() if n.node_definition not in INFRA_DEFINITIONS]
            running = sum(1 for n in nodes if n.is_active())
            title = lab.title or ""
            try:
                state = str(lab.state())
            except Exception:
                state = "UNKNOWN"
        except Exception as exc:  # one odd lab must not hide the rest
            out["labs"].append({"id": lab.id, "title": f"(unreadable: {exc})", "incident": None,
                                "kind": "other", "state": "UNKNOWN", "running": 0, "nodes": 0})
            continue
        incident = cml_api.incident_id_of(title)
        out["labs"].append({"id": lab.id, "title": title, "incident": incident, "kind": _kind(incident),
                            "state": state, "running": running, "nodes": len(nodes)})
    out["labs"].sort(key=lambda lab: (-lab["running"], lab["title"]))
    out["running"] = sum(lab["running"] for lab in out["labs"])
    return out


def _refresh() -> None:
    try:
        data = _read()
    except Exception as exc:
        data = {"ok": False, "error": f"{type(exc).__name__}: {exc}", "labs": [], "running": None, "cap": None}
    data["read_at"] = time.time()
    with _lock:
        _cache.update(at=time.monotonic(), data=data)


def status(force: bool = False) -> dict[str, Any]:
    """The newest reading; starts a refresh when the cached one is stale."""
    global _worker
    with _lock:
        fresh = _cache["data"] is not None and time.monotonic() - _cache["at"] < TTL_S
        if fresh and not force:
            return {**_cache["data"], "stale": False}
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_refresh, name="cml-status", daemon=True)
            _worker.start()
        worker = _worker
    worker.join(WAIT_S)
    with _lock:
        data = _cache["data"]
    if data is None:
        return {"ok": None, "pending": True, "labs": [], "running": None, "cap": None}
    return {**data, "stale": worker.is_alive()}


def invalidate() -> None:
    """Forget the reading (after a start or clean changes what runs)."""
    with _lock:
        _cache["at"] = 0.0
