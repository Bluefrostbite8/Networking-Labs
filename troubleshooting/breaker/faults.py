"""The fault library: load, validate, pick by tier and seed, inject, render.

A picked fault becomes an *instance*: the fault file with every {variable}
filled in from the lab, plus what the injection touched and the original
config of those sections. Instances are what the session seals as answers and
what the grader and selftest work from.
"""

from __future__ import annotations

import copy
import random
import string
from pathlib import Path
from typing import Any

from .inject_static import ACTIONS, TARGETS, InjectError, Vars
from .lab_model import LabModel, LabModelError, LabProfile, load_tool_yaml

FAULTS_DIR = Path(__file__).resolve().parent.parent / "faults"

TIER_DRAWS = {1: (1,), 2: (1, 2), 3: (2, 3)}        # blueprint: which fault tiers each session tier uses
TIER_COUNTS = {1: (1,), 2: (1, 2), 3: (2, 3)}       # how many faults per session
TIER_HINTS = {1: 3, 2: 3, 3: 2, 4: 1}               # hint levels available per session tier

REQUIRED = ("id", "title", "category", "tier", "mode", "platforms", "applies_when", "inject",
            "ticket", "hints", "checks", "reference_fix", "selftest", "debrief")


class FaultError(LabModelError):
    pass


class _Strict(dict):
    def __missing__(self, key: str) -> str:
        raise FaultError(f"template variable {{{key}}} has no value")


def render(obj: Any, values: Vars) -> Any:
    """Fill {variables} in every string of a nested structure (keys too)."""
    if isinstance(obj, str):
        if "{" not in obj:
            return obj
        fields = [f for _, f, _, _ in string.Formatter().parse(obj) if f]
        if len(fields) == 1 and obj == "{" + fields[0] + "}":
            return _Strict(values)[fields[0]]  # a lone {var} keeps its type (int, list)
        return obj.format_map(_Strict(values))
    if isinstance(obj, list):
        return [render(x, values) for x in obj]
    if isinstance(obj, dict):
        return {render(k, values): render(v, values) for k, v in obj.items()}
    return obj


def _lines(value: Any) -> list[str]:
    if isinstance(value, str):
        return [line for line in value.rstrip("\n").split("\n") if line.strip()]
    return [str(x) for x in value or []]


class Fault:
    def __init__(self, raw: dict[str, Any], path: Path) -> None:
        self.raw = raw
        self.path = path

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def tier(self) -> int:
        return int(self.raw["tier"])

    def proven_on(self, lab: str) -> bool:
        """A fault joins the library for a lab only after selftest passed there."""
        return lab[:2] in [str(x).zfill(2) for x in self.raw.get("selftest_passed") or []]

    @property
    def node_var(self) -> str:
        return self.raw["inject"].get("node_var", "node")

    # ---- acceptance (the parts that can be checked without a lab)

    def problems(self) -> list[str]:
        r = self.raw
        out = [f"missing {k}" for k in REQUIRED if k not in r]
        if out:
            return out
        if r["mode"] != "static":
            out.append(f"mode {r['mode']!r}: only static faults exist so far")
        if r["inject"].get("target") not in TARGETS:
            out.append(f"unknown target {r['inject'].get('target')!r}")
        if r["inject"].get("action") not in ACTIONS:
            out.append(f"unknown action {r['inject'].get('action')!r}")
        checks = r["checks"]
        if not checks.get("functional") and not r.get("state_only"):
            out.append("needs at least one functional check (or state_only: true)")
        if not checks.get("root_cause"):
            out.append("needs at least one root_cause check")
        if not checks.get("workaround_signatures"):
            out.append("needs at least one workaround signature")
        for h in ("h1", "h2", "h3"):
            if not (r["hints"] or {}).get(h):
                out.append(f"missing hint {h}")
        for t in ("t1", "t2", "t3"):
            if not (r["ticket"] or {}).get(t):
                out.append(f"missing ticket text {t}")
        d = r["debrief"] or {}
        for k in ("methods", "reasoning", "key_clue"):
            if not d.get(k):
                out.append(f"debrief needs {k}")
        cmds = d.get("commands") or {}
        if not cmds.get("show") or not cmds.get("debug"):
            out.append("debrief needs show and debug commands")
        st = r["selftest"] or {}
        if not st.get("reinject") or not st.get("workaround"):
            out.append("selftest needs reinject and workaround")
        return out

    # ---- where it fits

    def applies(self, model: LabModel, profile: LabProfile) -> bool:
        for cond in self.raw.get("applies_when") or []:
            (kind, value), = cond.items()
            if kind == "section":
                if not any(s.header.startswith(value) for n in model.ios_nodes() for s in n.config.sections()):
                    return False
            elif kind == "feature":
                if not any(value in n.config.features() for n in model.ios_nodes()):
                    return False
            elif kind == "link_between_feature_nodes":
                ok = any(model.node(lk.a.node).is_ios and model.node(lk.b.node).is_ios
                         and value in model.node(lk.a.node).config.features()
                         and value in model.node(lk.b.node).config.features() for lk in model.links)
                if not ok:
                    return False
            elif kind == "profile_intent":
                if value not in (profile.raw.get("intent") or {}):
                    return False
            elif kind == "dhcp_hosts":
                if not (profile.rebuild.get("renew_leases") or {}).get("hosts"):
                    return False
            else:
                raise FaultError(f"{self.id}: unknown applies_when condition {kind!r}")
        return True

    def candidates(self, model: LabModel, profile: LabProfile) -> list[Vars]:
        if not self.applies(model, profile):
            return []
        found = TARGETS[self.raw["inject"]["target"]](model, profile)
        platforms = set(self.raw["platforms"])
        return [v for v in found if model.node(v[self.node_var]).node_definition in platforms]

    # ---- injection

    def inject(self, healthy: LabModel, broken: LabModel, values: Vars, rng: random.Random,
               session_tier: int, index: int) -> dict[str, Any]:
        result = ACTIONS[self.raw["inject"]["action"]](broken, dict(values), rng)
        v = {**values, **result["vars"], "n": index}
        original = {}
        for node, header in result["touched"]:
            s = healthy.node(node).config.section(header)
            original.setdefault(node, {})[header] = list(s.children) if s else []
        r = self.raw
        tier_key = f"t{min(max(session_tier, 1), 3)}"
        checks = render(r["checks"], v)
        fix = render(r["reference_fix"], v)
        if isinstance(fix, str):
            fix = {v[self.node_var]: fix}
        st = render(r["selftest"], v)
        return {
            "index": index,
            "id": self.id,
            "title": r["title"],
            "category": r["category"],
            "state_only": bool(r.get("state_only")),
            "tier": self.tier,
            "vars": v,
            "touched": [list(t) for t in result["touched"]],
            "changes": result["changes"],
            "original_sections": original,
            "ticket": render(r["ticket"][tier_key], v),
            "hints": [render(r["hints"][h], v) for h in ("h1", "h2", "h3")],
            "checks": checks,
            "reference_fix": {node: _lines(lines) for node, lines in fix.items()},
            "selftest": {
                "reinject": {node: _lines(x) for node, x in (st.get("reinject") or {}).items()},
                "workaround": {node: _lines(x) for node, x in (st.get("workaround") or {}).items()},
                "workaround_saved": bool(st.get("workaround_saved", True)),
            },
            "debrief": render(r["debrief"], v),
        }


def library(folder: Path = FAULTS_DIR) -> list[Fault]:
    faults = []
    for path in sorted(folder.rglob("*.yaml")):
        raw = load_tool_yaml(path)
        if not isinstance(raw, dict) or "id" not in raw:
            raise FaultError(f"{path}: not a fault file")
        if raw["id"] != path.stem:
            raise FaultError(f"{path}: id {raw['id']!r} must match the file name")
        faults.append(Fault(raw, path))
    ids = [f.id for f in faults]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise FaultError(f"duplicate fault ids: {dupes}")
    return faults


def by_id(fault_id: str) -> Fault:
    for f in library():
        if f.id == fault_id:
            return f
    raise FaultError(f"no fault {fault_id!r} in {FAULTS_DIR}")


def _conflicts(touched: list[tuple[str, str]], values: Vars, taken: list[tuple[set, set]]) -> bool:
    """Engine rule 1: one fault per node and config section. Faults that share a
    host would also hide each other's symptom, so those never pair either."""
    sections = {tuple(t) for t in touched}
    hosts = {values[k] for k in ("host",) if values.get(k)}
    return any(sections & s or hosts & h for s, h in taken)


def pick(model: LabModel, profile: LabProfile, tier: int, seed: int,
         only: list[str] | None = None) -> tuple[LabModel, list[dict[str, Any]]]:
    """Choose faults and inject them into a copy of ``model``. The same lab, tier
    and seed always give the same faults, targets and values (engine rule 6)."""
    if tier not in TIER_DRAWS:
        raise FaultError(f"tier {tier}: only T1 to T3 draw single faults (T4 uses combos)")
    rng = random.Random(seed)
    lib = library()
    bad = {f.id: f.problems() for f in lib if f.problems()}
    if only:
        pool = []
        for fid in only:
            f = next((x for x in lib if x.id == fid), None)
            if f is None:
                raise FaultError(f"no fault {fid!r}")
            pool.append(f)
        count = len(only)
    else:
        pool = [f for f in lib if f.tier in TIER_DRAWS[tier] and f.id not in bad and f.proven_on(profile.lab)]
        rng.shuffle(pool)
        count = rng.choice(TIER_COUNTS[tier])
    for f in pool:
        if f.id in bad:
            raise FaultError(f"{f.id} fails acceptance: {'; '.join(bad[f.id])}")
    broken = LabModel(copy.deepcopy(model.data), source=model.source)
    chosen: list[dict[str, Any]] = []
    taken: list[tuple[set, set]] = []
    for f in pool:
        if len(chosen) >= count:
            break
        cands = f.candidates(model, profile)
        rng.shuffle(cands)
        for values in cands:
            trial = LabModel(copy.deepcopy(broken.data), source=model.source)
            try:
                inst = f.inject(model, trial, values, rng, tier, len(chosen) + 1)
            except InjectError:
                continue
            if _conflicts(inst["touched"], inst["vars"], taken):
                continue
            broken = trial
            chosen.append(inst)
            taken.append(({tuple(t) for t in inst["touched"]},
                          {inst["vars"][k] for k in ("host",) if inst["vars"].get(k)}))
            break
        else:
            if only:
                raise FaultError(f"{f.id} has no place in {profile.lab} that fits")
    if not chosen:
        raise FaultError(f"no fault that passed selftest on {profile.lab} fits tier {tier} "
                         f"(run: python -m breaker selftest <fault> --lab {profile.lab[:2]})")
    return broken, chosen
