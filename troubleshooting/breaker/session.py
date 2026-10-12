"""A break-fix session on disk: sessions/<date>_<lab>_t<tier>_s<seed>/.

    session.json    lab, tier, seed, incident id, fault count, times (no answers)
    broken.yaml     the lab as imported, faults included
    ticket.md       what the user sees (report.render_ticket)
    answers.sealed  the fault instances, base64-sealed; gitignored, never published
    answers.json    the same instances in plain JSON, written by the debrief (published)
    hints.log       every hint taken, timestamped
    baseline_before.json, grade.json, evidence/, debrief.md
"""

from __future__ import annotations

import base64
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .faults import TIER_HINTS
from .lab_model import TOOL_ROOT, LabModel, LabModelError, LabProfile

SESSIONS_DIR = TOOL_ROOT / "sessions"
SEALED = "answers.sealed"     # gitignored: the answer key while the session is open
REVEALED = "answers.json"     # plain JSON, written by the debrief and published


class SessionError(LabModelError):
    pass


def seal(data: Any) -> str:
    return base64.b64encode(json.dumps(data, indent=2).encode("utf-8")).decode("ascii")


def unseal(text: str) -> Any:
    return json.loads(base64.b64decode(text.encode("ascii")).decode("utf-8"))


class Session:
    def __init__(self, path: Path) -> None:
        self.path = Path(path).resolve()
        meta = path / "session.json"
        if not meta.exists():
            raise SessionError(f"{path} is not a session folder (no session.json)")
        self.meta: dict[str, Any] = json.loads(meta.read_text(encoding="utf-8"))

    # ---- creation and lookup

    @classmethod
    def create(cls, profile: LabProfile, broken: LabModel, instances: list[dict[str, Any]],
               tier: int, seed: int, ticket: Callable[[str, Path], str], base: Path = SESSIONS_DIR,
               incident: str | None = None) -> "Session":
        stem = f"{datetime.now():%Y-%m-%d}_{profile.lab}_t{tier}_s{seed}"
        path = base / stem
        n = 2
        while path.exists():
            path = base / f"{stem}-{n}"
            n += 1
        path.mkdir(parents=True)
        # Short incident id: CML caps lab titles at 64 characters (engine rule 8).
        incident = incident or f"{profile.lab[:2]}-t{tier}-s{seed}" + (f"-{n - 1}" if n > 2 else "")
        meta = {"session": path.name, "lab": profile.lab, "tier": tier, "seed": seed, "incident": incident,
                "faults": len(instances), "hint_levels": TIER_HINTS[tier],
                "created": datetime.now().isoformat(timespec="seconds")}
        (path / "session.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        (path / "broken.yaml").write_text(broken.to_yaml(), encoding="utf-8")
        (path / SEALED).write_text(seal(instances), encoding="utf-8")
        (path / "ticket.md").write_text(ticket(incident, path), encoding="utf-8")
        (path / "hints.log").write_text("", encoding="utf-8")
        return cls(path)

    @classmethod
    def find(cls, ref: str) -> "Session":
        """By folder name, path, or incident id."""
        p = Path(ref)
        if (p / "session.json").exists():
            return cls(p)
        if (SESSIONS_DIR / ref / "session.json").exists():
            return cls(SESSIONS_DIR / ref)
        matches = [d for d in sorted(SESSIONS_DIR.glob("*")) if (d / "session.json").exists()
                   and json.loads((d / "session.json").read_text(encoding="utf-8")).get("incident") == ref]
        if len(matches) == 1:
            return cls(matches[0])
        raise SessionError(f"no session {ref!r} (give the folder name under sessions/ or the incident id)")

    # ---- contents

    @property
    def incident(self) -> str:
        return self.meta["incident"]

    @property
    def tier(self) -> int:
        return int(self.meta["tier"])

    def profile(self) -> LabProfile:
        return LabProfile.load(self.meta["lab"])

    def broken_model(self) -> LabModel:
        return LabModel.load(self.path / "broken.yaml")

    def answers(self) -> list[dict[str, Any]]:
        sealed = self.path / SEALED
        if sealed.exists():
            return unseal(sealed.read_text(encoding="utf-8"))
        text = (self.path / REVEALED).read_text(encoding="utf-8")
        # Sessions created before build step 5 sealed answers.json itself.
        return json.loads(text) if text.lstrip().startswith("[") else unseal(text)

    @property
    def revealed(self) -> bool:
        path = self.path / REVEALED
        return path.exists() and path.read_text(encoding="utf-8").lstrip().startswith("[")

    def reveal(self) -> Path:
        """Write the answers in plain JSON for publishing; the sealed copy stays (ignored)."""
        answers = self.answers()
        sealed = self.path / SEALED
        if not sealed.exists():  # pre step 5 session: keep the sealed text under its new name
            sealed.write_text(seal(answers), encoding="utf-8")
        return self.write_json(REVEALED, answers)

    def update(self, **fields: Any) -> None:
        self.meta.update(fields)
        (self.path / "session.json").write_text(json.dumps(self.meta, indent=2), encoding="utf-8")

    def write_json(self, name: str, data: Any) -> Path:
        path = self.path / name
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        return path

    def read_json(self, name: str) -> Any:
        path = self.path / name
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    # ---- hints: strictly in order, capped by tier, every one logged

    def hints_taken(self) -> dict[int, int]:
        taken: dict[int, int] = {}
        for line in (self.path / "hints.log").read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 3:
                fault, level = int(parts[1]), int(parts[2].lstrip("h"))
                taken[fault] = max(taken.get(fault, 0), level)
        return taken

    def take_hint(self, fault: int) -> tuple[int, str]:
        if self.meta.get("debriefed"):
            raise SessionError("this session is debriefed: the answers are already revealed")
        answers = self.answers()
        if not 1 <= fault <= len(answers):
            raise SessionError(f"fault number must be 1 to {len(answers)}")
        level = self.hints_taken().get(fault, 0) + 1
        cap = int(self.meta["hint_levels"])
        if level > cap:
            raise SessionError(f"fault {fault}: all {cap} hint level(s) for tier T{self.tier} already taken")
        text = answers[fault - 1]["hints"][level - 1]
        with (self.path / "hints.log").open("a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now().isoformat(timespec='seconds')}\t{fault}\th{level}\t{text}\n")
        return level, text
