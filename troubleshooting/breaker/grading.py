"""Grade decision order (blueprint: Grading, hints and scoring).

Each fault walks five questions and stops at the first "no":

    1. All functional checks pass?        no -> Partial (some) or Open (none)
    2. Root cause gone (state check)?     no -> Temporary (workaround)
    3. Running matches startup?           no -> Temporary (unsaved)
    4. Design intent still true?          no -> Off-design
    5. Section matches original config?   no -> Equivalent, yes -> Exact

Questions 1, 2 and 4 read live state only. Question 3 compares running and
startup config for the sections the fault touched (persistence), and question 5
compares those sections with the original lab (Exact vs Equivalent); neither
decides whether the network works.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Callable

from .checks import INTENT_KINDS, Checker, Result
from .lab_model import IosConfig

WEIGHTS = {"Exact": 1.0, "Equivalent": 1.0, "Off-design": 0.7, "Temporary": 0.4, "Open": 0.0}
HINT_PENALTY = {0: 0, 1: 10, 2: 30, 3: 65}   # cumulative: h1 10, h1+h2 30, h1+h2+h3 65
COLLATERAL_PENALTY = 15

# Lines that change on their own and say nothing about a fix: port security
# learns a new host MAC after every import or host restart.
VOLATILE = [re.compile(r"^switchport port-security mac-address sticky [0-9a-f.]+$")]


def normalize(children: list[str] | None) -> list[str]:
    out = set()
    for c in children or []:
        line = c.strip()
        if not line or line == "!" or any(rx.match(line) for rx in VOLATILE):
            continue
        out.add(line)
    return sorted(out)


def _section(cfg: IosConfig, header: str) -> list[str] | None:
    """A section's lines, or None when it is absent. A global line with no
    children (spanning-tree vlan 10 priority 0) counts as its own header, so its
    presence is compared like any other line."""
    s = cfg.section(header)
    if s is None:
        return None
    return list(s.children) if s.children else [s.header]


class Grader:
    def __init__(self, checker: Checker, run: Callable[[str, str], str],
                 refresh_hosts: Callable[[list[str]], None], log: Callable[[str], None]) -> None:
        self.checker = checker
        self.run = run
        self.refresh_hosts = refresh_hosts
        self.log = log
        self._configs: dict[tuple[str, str], IosConfig] = {}

    def _config(self, node: str, which: str) -> IosConfig:
        key = (node, which)
        if key not in self._configs:
            self._configs[key] = IosConfig(self.run(node, f"show {which}-config"))
        return self._configs[key]

    def _config_now(self, inst: dict[str, Any]) -> list[dict[str, Any]] | None:
        """The faulted sections as they run now, against the broken and the original
        config, so the debrief can show what the user changed. Not used to grade."""
        broken = {(c["node"], c["section"]): c.get("after") or [] for c in inst.get("changes") or []}
        out = []
        try:
            for node, header in inst["touched"]:
                now = normalize(_section(self._config(node, "running"), header) or [])
                was = normalize(broken.get((node, header), []))
                orig = normalize(inst["original_sections"].get(node, {}).get(header))
                out.append({"node": node, "section": header,
                            "vs_broken": {"added": sorted(set(now) - set(was)), "removed": sorted(set(was) - set(now))},
                            "vs_original": {"added": sorted(set(now) - set(orig)),
                                            "removed": sorted(set(orig) - set(now))}})
        except Exception as exc:  # a console hiccup here must never change a grade
            self.log(f"fault {inst['index']}: could not read the running config for the debrief ({exc})")
            return None
        return out

    def _run_items(self, items: list[dict]) -> list[Result]:
        out: list[Result] = []
        for item in items or []:
            if "unsaved_fix" in item:  # marker, not a check: the Temporary path is an unsaved fix (question 3)
                continue
            out += self.checker.run_item(item)
        return out

    def _intent(self, kinds: list[str]) -> list[Result]:
        intent = self.checker.profile.raw.get("intent") or {}
        wanted = INTENT_KINDS if kinds == "all" else kinds
        out: list[Result] = []
        for kind in wanted:
            if kind in intent:
                out += getattr(self.checker, f"intent_{kind}")(intent[kind])
        return out

    def grade_fault(self, inst: dict[str, Any]) -> dict[str, Any]:
        checks = inst["checks"]
        self.checker.fresh()
        self._configs.clear()
        steps: list[dict[str, Any]] = []
        record: dict[str, Any] = {"index": inst["index"], "id": inst["id"], "title": inst["title"], "steps": steps}

        if checks.get("refresh"):
            self.log(f"fault {inst['index']}: restarting {', '.join(checks['refresh'])} to renew DHCP")
            self.refresh_hosts(list(checks["refresh"]))
            self.checker.fresh()

        def done(grade: str, weight: float) -> dict[str, Any]:
            record.update(grade=grade, weight=round(weight, 3))
            record["config_now"] = self._config_now(inst)  # for the debrief only, never the grade
            return record

        # 1. functional
        functional = [] if inst.get("state_only") else self._run_items(checks.get("functional"))
        record["functional"] = [r.as_dict() for r in functional]
        passed = sum(r.ok for r in functional)
        ok = passed == len(functional)
        steps.append({"question": "functional", "ok": ok, "detail": f"{passed}/{len(functional)} pass"})
        if not ok:
            if passed:
                return done("Partial", 0.25 * passed / len(functional))
            return done("Open", 0.0)

        # 2. root cause, by state
        root = self._run_items(checks.get("root_cause"))
        record["root_cause"] = [r.as_dict() for r in root]
        ok = all(r.ok for r in root)
        steps.append({"question": "root_cause", "ok": ok,
                      "detail": "; ".join(r.detail for r in root)})
        if not ok:
            signatures = self._run_items(checks.get("workaround_signatures"))
            record["workarounds"] = [r.as_dict() for r in signatures if r.ok]
            return done("Temporary", WEIGHTS["Temporary"])

        # 3. persistence: running vs startup for every touched section
        unsaved = []
        for node, header in inst["touched"]:
            run_sec = _section(self._config(node, "running"), header)
            start_sec = _section(self._config(node, "startup"), header)
            if normalize(run_sec) != normalize(start_sec):
                unsaved.append(f"{node} {header}")
        ok = not unsaved
        steps.append({"question": "saved", "ok": ok,
                      "detail": "running matches startup" if ok else f"not saved: {', '.join(unsaved)}"})
        if not ok:
            return done("Temporary", WEIGHTS["Temporary"])

        # 4. design intent
        intent = self._intent(checks.get("intent") or [])
        record["intent"] = [r.as_dict() for r in intent]
        ok = all(r.ok for r in intent)
        steps.append({"question": "intent", "ok": ok,
                      "detail": "; ".join(r.detail for r in intent if not r.ok) or "intact"})
        if not ok:
            return done("Off-design", WEIGHTS["Off-design"])

        # 5. original config of the touched sections
        differs = []
        for node, header in inst["touched"]:
            now = normalize(_section(self._config(node, "running"), header) or [])
            orig = normalize(inst["original_sections"].get(node, {}).get(header))
            if now != orig:
                differs.append({"node": node, "section": header,
                                "added": sorted(set(now) - set(orig)), "removed": sorted(set(orig) - set(now))})
        record["original_diff"] = differs
        steps.append({"question": "original", "ok": not differs,
                      "detail": "matches the original" if not differs else
                      ", ".join(f"{d['node']} {d['section']}" for d in differs) + " differ"})
        return done("Equivalent" if differs else "Exact", WEIGHTS["Equivalent" if differs else "Exact"])


_IP = r"\d{1,3}(?:\.\d{1,3}){3}"


def _ping_targets(evidence: list[dict[str, str]]) -> set[str]:
    """Addresses a ping check sent echoes to, read from its evidence commands."""
    return {m.group(1) for e in evidence or []
            if (m := re.match(rf"^ping (?:-c \d+ )?({_IP})$", e.get("command", "")))}


def _failed_targets(detail: str) -> set[str]:
    """Addresses in the 'failed: ...' part of a ping check's detail."""
    _, _, failed = detail.partition("failed: ")
    return set(re.findall(rf"({_IP}) \(", failed.split(";")[0]))


def collateral(before: list[dict[str, Any]], after: list[Result]) -> list[str]:
    """Baseline checks that passed when the session started and fail now.

    A ping check that pings every DHCP lease grows when the grader restarts a host
    to renew its lease: that host is pinged for the first time at grading, and if
    the fault it belongs to is still unfixed it fails. That failure is the fault's
    own symptom (already graded), not damage the student did, so a ping check
    whose only failures are addresses it never pinged at session start is excused."""
    def keyed(items):
        seen: dict[str, int] = {}
        out = {}
        for name, ok, detail, evidence in items:
            n = seen[name] = seen.get(name, 0) + 1
            out[f"{name}#{n}"] = (ok, detail, evidence)
        return out

    b = keyed((r["name"], r["ok"], r["detail"], r.get("evidence")) for r in before if not r.get("optional"))
    a = keyed((r.name, r.ok, r.detail, r.evidence) for r in after if not r.optional)
    lost = []
    for k, (ok, detail, evidence) in a.items():
        if k not in b or not b[k][0] or ok:
            continue
        failed = _failed_targets(detail)
        if failed and not failed & _ping_targets(b[k][2]):
            continue  # only new targets fail: a renewed host, not collateral
        lost.append(f"{k.split('#')[0]}: {detail}")
    return lost


def score(record: dict[str, Any], hints: int, collateral_count: int) -> int:
    raw = 100 * record["weight"] - HINT_PENALTY[min(hints, 3)] - COLLATERAL_PENALTY * collateral_count
    return max(0, round(raw))


def evidence_files(records: list[dict[str, Any]]) -> dict[str, str]:
    """{file name: text} for evidence/: every command output the grader judged."""
    files: dict[str, str] = {}
    for rec in records:
        for part in ("functional", "root_cause", "intent", "workarounds"):
            for i, res in enumerate(rec.get(part) or [], 1):
                blocks = [f"# {res['name']}: {'PASS' if res['ok'] else 'FAIL'} ({res['detail']})"]
                for ev in res.get("evidence") or []:
                    blocks.append(f"\n## {ev['node']}# {ev['command']}\n{ev['output'].rstrip()}")
                files[f"fault{rec['index']}-{part}-{i}.txt"] = "\n".join(blocks) + "\n"
    return files


def stamp() -> str:
    return datetime.now().isoformat(timespec="seconds")
