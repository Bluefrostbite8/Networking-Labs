"""What the GUI shows, read from the same files the CLI writes. Nothing here
reveals a fault before its debrief: an open session exposes only its ticket,
hints taken, the grade lines the CLI prints, and your notes."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from ..lab_model import REPO_ROOT, TOOL_ROOT, LabModelError, LabProfile, lab_dirs
from ..report import TIER_NAMES, LabDoc, read_hints
from ..session import SESSIONS_DIR

RUNS_DIR = TOOL_ROOT / "runs"
STATES = ["created", "started", "graded", "debriefed"]
HINT_STEP = {1: 10, 2: 20, 3: 35}  # penalty added by each level (total 10, 30, 65)

# Files an open (not yet debriefed) session may show: nothing that names the fault.
OPEN_SESSION_FILES = {"ticket.md", "notes.md", "hints.log"}
NEVER_SERVED = {"config.yaml", ".env", "answers.sealed"}
SERVED_SUFFIXES = {".md", ".svg", ".png", ".jpg", ".jpeg", ".txt", ".json", ".yaml", ".log", ".pcap"}


def _json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def lab_name(lab: str) -> str:
    try:
        return LabDoc(LabProfile.load(lab)).name
    except (LabModelError, OSError):
        return lab


def session_state(meta: dict[str, Any], folder: Path) -> str:
    if meta.get("debriefed"):
        return "debriefed"
    if (folder / "grade.json").exists():
        return "graded"
    if meta.get("started"):
        return "started"
    return "created"


def session_info(folder: Path) -> dict[str, Any] | None:
    meta = _json(folder / "session.json")
    if not meta:
        return None
    grade = _json(folder / "grade.json")
    state = session_state(meta, folder)
    hints = read_hints(folder / "hints.log")
    faults = int(meta.get("faults", 0))
    taken = {i: max([int(h["level"].lstrip("h")) for h in hints if h["fault"] == i] or [0])
             for i in range(1, faults + 1)}
    cap = int(meta.get("hint_levels", 3))
    hint_rows = []
    for i in range(1, faults + 1):
        nxt = taken[i] + 1
        hint_rows.append({"fault": i, "taken": [h for h in hints if h["fault"] == i],
                          "next": nxt if nxt <= cap else None, "cost": HINT_STEP.get(nxt)})
    return {
        "name": folder.name, "path": folder, "meta": meta, "state": state, "state_index": STATES.index(state),
        "incident": meta["incident"], "lab": meta["lab"], "lab_name": lab_name(meta["lab"]),
        "tier": int(meta["tier"]), "tier_name": TIER_NAMES.get(int(meta["tier"]), f"T{meta['tier']}"),
        "seed": meta.get("seed"), "faults": faults, "created": meta.get("created"),
        "started": meta.get("started"), "debriefed": meta.get("debriefed"),
        "grade": grade, "score": grade.get("score") if grade else None,
        "grades": [f["grade"] for f in (grade or {}).get("faults", [])],
        "hints": hint_rows, "hints_total": len(hints),
        "notes": (folder / "notes.md").read_text(encoding="utf-8") if (folder / "notes.md").exists() else "",
        "has_debrief": (folder / "debrief.md").exists(),
    }


def sessions() -> list[dict[str, Any]]:
    if not SESSIONS_DIR.exists():
        return []
    out = [session_info(d) for d in SESSIONS_DIR.iterdir() if d.is_dir()]
    return sorted([s for s in out if s], key=lambda s: s.get("created") or "", reverse=True)


def find_session(name: str) -> dict[str, Any] | None:
    folder = (SESSIONS_DIR / name).resolve()
    if folder.parent != SESSIONS_DIR.resolve() or not folder.is_dir():
        return None
    return session_info(folder)


def _healthy_runs(lab: str) -> tuple[dict | None, dict | None]:
    """(newest passing healthy baseline, newest healthy result of any kind)."""
    passing = latest = None
    for path in sorted(RUNS_DIR.glob(f"{lab[:2]}-healthy-*-baseline.json"), reverse=True):
        data = _json(path)
        if not data or data.get("lab") != lab:
            continue
        data["when"] = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        latest = latest or data
        if data.get("total") and data.get("passed") == data.get("total"):
            passing = data
            break
    return passing, latest


def labs() -> list[dict[str, Any]]:
    from ..faults import library

    lib = library()
    out = []
    for d in lab_dirs():
        row: dict[str, Any] = {"lab": d.name, "number": d.name[:2], "name": d.name, "profile": False,
                               "proven": [], "fit": [], "healthy": None}
        try:
            profile = LabProfile.load(d.name)
            model = profile.model()
        except LabModelError:
            out.append(row)
            continue
        row.update(profile=True, name=LabDoc(profile).name, nodes=len(model.nodes),
                   licensed=model.licensed_node_count())
        for f in lib:
            if f.candidates(model, profile):
                row["fit"].append(f.id)
                if f.proven_on(d.name):
                    row["proven"].append(f.id)
        row["healthy"], row["healthy_latest"] = _healthy_runs(d.name)
        out.append(row)
    return out


def selftest_results() -> dict[tuple[str, str], dict[str, Any]]:
    """{(fault id, lab number): newest selftest result}."""
    out = {}
    for path in RUNS_DIR.glob("selftest-*-*.json"):
        data = _json(path)
        if not data or "fault" not in data:
            continue
        data["when"] = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        out[(data["fault"], str(data.get("lab", ""))[:2])] = data
    return out


def fault_rows() -> list[dict[str, Any]]:
    from ..faults import library

    results = selftest_results()
    rows = []
    for f in library():
        proven = [str(x).zfill(2) for x in f.raw.get("selftest_passed") or []]
        tests = {lab: r for (fid, lab), r in results.items() if fid == f.id}
        rows.append({"id": f.id, "title": f.raw.get("title", ""), "category": f.raw.get("category", ""),
                     "tier": f.tier, "proven": proven, "tests": tests, "problems": f.problems(),
                     "path": f.path})
    return sorted(rows, key=lambda r: (r["category"], r["tier"], r["id"]))


def mark_proven(fault_id: str, lab_number: str) -> str:
    """Add a lab to selftest_passed, only when that lab's newest selftest passed."""
    from ..faults import by_id

    result = selftest_results().get((fault_id, lab_number))
    if not result or not result.get("passed"):
        raise ValueError(f"no passing selftest of {fault_id} on lab {lab_number} in runs/")
    fault = by_id(fault_id)
    text = fault.path.read_text(encoding="utf-8")
    m = re.search(r"^selftest_passed:\s*\[(.*?)\](.*)$", text, re.M)
    if not m:
        raise ValueError(f"{fault.path.name} has no selftest_passed line")
    labs_now = [x.strip().strip("'\"") for x in m.group(1).split(",") if x.strip()]
    if lab_number in labs_now:
        return f"{fault_id} is already proven on lab {lab_number}"
    labs_now = sorted(set(labs_now) | {lab_number})
    new = "selftest_passed: [" + ", ".join(f'"{x}"' for x in labs_now) + "]"
    comment = m.group(2)
    if comment.strip():
        new = new.ljust(42) + comment.strip()
    fault.path.write_text(text[:m.start()] + new + text[m.end():], encoding="utf-8")
    return f"{fault_id} is now proven on lab {lab_number} ({fault.path.relative_to(TOOL_ROOT).as_posix()})"


def servable(path: Path) -> tuple[bool, str]:
    """Whether /files may serve this path, and why not."""
    try:
        path = path.resolve()
        rel = path.relative_to(REPO_ROOT.resolve())
    except (OSError, ValueError):
        return False, "outside the repo"
    if any(part.startswith(".") for part in rel.parts) or path.name in NEVER_SERVED:
        return False, "private file"
    if path.suffix.lower() not in SERVED_SUFFIXES:
        return False, "file type not served"
    sessions = SESSIONS_DIR.resolve()
    if sessions in path.parents:
        folder = sessions / path.relative_to(sessions).parts[0]
        meta = _json(folder / "session.json") or {}
        if not meta.get("debriefed") and path.name not in OPEN_SESSION_FILES:
            return False, "hidden until the debrief"
    return True, ""
