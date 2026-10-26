"""ticket.md and debrief.md in the repo's README style (blueprint: Ticket, debrief
and methods), the sanitizer pass before publishing, and the session index.

    render_ticket()    at break: the complaint in the tier's voice, the lab, the
                       rules and what "fixed" looks like. Never names the fault.
    render_debrief()   after grade: per fault what was broken, the grade and why,
                       where to look, the method, the commands and the output line
                       that gives it away (quoted from evidence/), a better fix.
    sanitize_session() strips banner blocks and EULA text from every file that
                       gets published.
    update_index()     the Session Index table in troubleshooting/README.md.

Everything here reads files only; nothing talks to CML.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .lab_model import EULA_RE, TOOL_ROOT, LabModel, LabProfile, strip_banners

TIER_NAMES = {1: "T1 Guided", 2: "T2 Standard", 3: "T3 Field", 4: "T4 Escalation"}
TIER_VOICE = {
    1: "names the device and the symptom",
    2: "the user's view, no device named",
    3: "a vague help-desk complaint",
    4: "one complaint for everything",
}
HINT_PENALTY_ROWS = [("h1", 10, 10), ("h1 + h2", 20, 30), ("h1 + h2 + h3", 35, 65)]

# Where each fault category sits, for "Where to look" (symptom > scope > layer > ...).
CATEGORY_LAYER = {
    "l1": "Layer 1: link and admin state",
    "l2": "Layer 2: VLANs and trunks",
    "stp": "Layer 2: spanning tree",
    "etherchannel": "Layer 2: EtherChannel",
    "l3": "Layer 3: addressing",
    "static": "Layer 3: static routing",
    "ospf": "Layer 3: OSPF",
    "eigrp": "Layer 3: EIGRP",
    "fhrp": "Layer 3: first hop (HSRP)",
    "services": "Services: DHCP and NAT",
    "security": "Security: ACLs and port security",
    "mgmt": "Management plane",
    "wireless": "Wireless: WLC and RF",
}

# The methods table from the blueprint. Pages arrive in build step 7; until a page
# exists the debrief names the method without a link.
METHODS = {
    "bottom_up_osi": ("Bottom-up (OSI)", "one segment has lost everything: start at Layer 1 and climb"),
    "top_down": ("Top-down", "one application fails, but ping works"),
    "divide_and_conquer": ("Divide and conquer", "the ticket is vague: start at Layer 3 with ping, then move up or down"),
    "follow_the_path": ("Follow the path", "traffic dies somewhere along a path: traceroute, then routing, ARP and MAC tables hop by hop"),
    "compare_known_good": ("Compare known good", "a redundant peer works and its twin doesn't"),
    "spot_the_difference": ("Spot the difference", "a device disagrees with the lab README or the profile's intent"),
}
METHOD_ALIASES = {"bottom_up": "bottom_up_osi"}

# Known Good: how each baseline check kind reads in a ticket, and how to verify it.
CHECK_LABELS = {
    "stp_root": ("Spanning-tree root", "show spanning-tree vlan <vlan>"),
    "hsrp_active": ("HSRP active gateway", "show standby brief"),
    "ospf_neighbors": ("OSPF neighbors", "show ip ospf neighbor"),
    "eigrp_neighbors": ("EIGRP neighbors", "show ip eigrp neighbors"),
    "etherchannel": ("EtherChannel bundles", "show etherchannel summary"),
    "route_via": ("Preferred paths", "show ip route <prefix>"),
    "vtp": ("VTP", "show vtp status"),
    "vlans": ("VLANs", "show vlan brief"),
    "trunks": ("Trunks", "show interfaces trunk"),
    "port_security": ("Port security", "show port-security interface <port>"),
    "dhcp_bindings": ("DHCP bindings", "show ip dhcp binding"),
    "ping": ("Reachability", "ping <address>"),
    "mst_region": ("MST region", "show spanning-tree mst configuration"),
    "stp_port": ("Spanning-tree port roles", "show spanning-tree interface <port>"),
    "stp_inconsistent": ("Inconsistent ports", "show spanning-tree inconsistentports"),
    "stp_summary": ("Spanning-tree mode", "show spanning-tree summary"),
    "wlan": ("WLAN", "show wlan summary"),
}

QUESTIONS = {
    "functional": "Does traffic work?",
    "root_cause": "Is the root cause gone?",
    "saved": "Is the fix saved?",
    "intent": "Is the design intact?",
    "original": "Does the section match the original?",
}

COLORS = {"blue": "1BA0D7", "navy": "005073", "teal": "0b7285", "green": "047857", "amber": "b45309",
          "red": "b91c1c", "purple": "6d28d9", "indigo": "1d4ed8"}
GRADE_COLORS = {"Exact": "green", "Equivalent": "green", "Off-design": "amber", "Temporary": "amber",
                "Partial": "red", "Open": "red"}

# Lines that change on every import and say nothing about a fault or a fix.
VOLATILE = re.compile(r"^switchport port-security mac-address sticky [0-9a-f.]+$")


# --------------------------------------------------------------------------- small helpers


def badge(label: str, message: Any, color: str, logo: str | None = None) -> str:
    """A shields.io badge in the repo's style (dashes and underscores escaped)."""
    def esc(text: Any) -> str:
        return quote(str(text).replace("-", "--").replace("_", "__"), safe="")
    url = f"https://img.shields.io/badge/{esc(label)}-{esc(message)}-{COLORS.get(color, color)}"
    if logo:
        url += f"?logo={logo}&logoColor=white"
    return f"![{label}]({url})"


def cell(text: Any) -> str:
    """Safe inside a markdown table cell."""
    return str(text).replace("|", "\\|").replace("\n", " ").strip()


def rel(target: Path, start: Path) -> str:
    return Path(os.path.relpath(target, start)).as_posix()


def stamp(iso: str | None) -> str:
    if not iso:
        return "unknown"
    try:
        return datetime.fromisoformat(iso).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def _norm(lines: list[str]) -> list[str]:
    out = []
    for line in lines or []:
        s = line.strip()
        if s and s != "!" and not VOLATILE.match(s):
            out.append(s)
    return out


def _diff(before: list[str], after: list[str]) -> tuple[list[str], list[str]]:
    b, a = _norm(before), _norm(after)
    return [x for x in b if x not in a], [x for x in a if x not in b]


def _diff_block(node: str, section: str, removed: list[str], added: list[str]) -> list[str]:
    out = ["```diff", f"  ! {node}, {section}"]
    out += [f"- {x}" for x in removed] + [f"+ {x}" for x in added]
    out.append("```")
    return out


def _fix_block(fix: dict[str, list[str]]) -> list[str]:
    out = ["```text"]
    for node, lines in fix.items():
        out.append(f"! {node}")
        out += [x.rstrip() for x in lines]
        out.append(f"! then on {node}: write memory")
    out.append("```")
    return out


# --------------------------------------------------------------------------- the lab README


class LabDoc:
    """What the ticket borrows from the lab's own README: name, objective,
    topology diagram and node inventory."""

    def __init__(self, profile: LabProfile) -> None:
        self.folder = profile.source.parent
        readme = profile.raw.get("readme")
        path = (profile.path.parent / readme).resolve() if readme else self.folder / "README.md"
        self.readme = path
        self.text = path.read_text(encoding="utf-8") if path.exists() else ""
        h1 = re.search(r"^# (.+)$", self.text, re.M)
        self.title = h1.group(1).strip() if h1 else profile.lab
        self.name = self.title.split(" — ")[0].strip()
        self.number = profile.lab[:2]

    def section(self, heading: str) -> str:
        m = re.search(rf"^## {re.escape(heading)}\s*$(.*?)(?=^## |\Z)", self.text, re.M | re.S)
        return m.group(1).strip() if m else ""

    def objective(self) -> str:
        body = self.section("Objective")
        return body.split("\n\n")[0].strip() if body else ""

    def inventory(self) -> list[str]:
        body = self.section("Node Inventory")
        return [line for line in body.splitlines() if line.startswith("|")]

    @property
    def svg(self) -> Path | None:
        p = self.folder / "topology.svg"
        return p if p.exists() else None


# --------------------------------------------------------------------------- ticket


def known_good_rows(results: list[dict[str, Any]]) -> list[tuple[str, str, str, str]]:
    """(check, where, healthy result, verify with) per check kind, from a healthy run."""
    kinds: dict[str, list[dict[str, Any]]] = {}
    for r in results:
        kinds.setdefault(r["name"].split()[0], []).append(r)
    rows = []
    for kind, items in kinds.items():
        label, verify = CHECK_LABELS.get(kind, (kind.replace("_", " ").capitalize(), ""))
        if not verify:
            ev = (items[0].get("evidence") or [{}])[0]
            verify = ev.get("command", "")
        where: list[str] = []
        by_detail: dict[str, list[str]] = {}
        for r in items:
            for ev in r.get("evidence") or []:
                if ev.get("node") and ev["node"] not in where:
                    where.append(ev["node"])
            target = r["name"][len(kind):].strip()
            target = re.sub(r"^vlan\b", "VLAN", target)
            detail = r["detail"] + (" (optional)" if r.get("optional") else "")
            by_detail.setdefault(detail, []).append(target)
        result = "; ".join((f"{', '.join(t for t in targets if t)}: {d}" if any(targets) else d)
                           for d, targets in by_detail.items())
        rows.append((label, ", ".join(where), result, f"`{verify}`" if verify else ""))
    return rows


def render_ticket(profile: LabProfile, model: LabModel, instances: list[dict[str, Any]], tier: int,
                  seed: int, incident: str, session_dir: Path, created: str | None = None,
                  healthy: dict[str, Any] | None = None) -> str:
    """The ticket the user works from. It must never name the fault, the node or
    the section: only the complaint in the tier's voice."""
    doc = LabDoc(profile)
    created = created or datetime.now().isoformat(timespec="seconds")
    lines = [f"# Incident {incident}: {doc.name}", ""]
    lines += [
        badge("Platform", "Cisco Modeling Labs", "blue", "cisco"),
        badge("Lab", f"{doc.number} {doc.name}", "navy"),
        badge("Tier", TIER_NAMES.get(tier, f"T{tier}"), "amber"),
        badge("Faults", len(instances), "red"),
        badge("Seed", seed, "purple"),
        "",
        "## Incident Report",
        "",
        "| Reported | Tier | Ticket voice |",
        "|----------|------|--------------|",
        f"| {stamp(created)} | {TIER_NAMES.get(tier, tier)} | {TIER_VOICE.get(tier, '')} |",
        "",
    ]
    if len(instances) == 1:
        lines += [f"> {instances[0]['ticket']}", ""]
    else:
        lines += [f"{i['index']}. {i['ticket']}" for i in instances] + [""]
        lines += ["Each numbered complaint is one fault; hints and grades use the same numbers.", ""]

    lines += ["## Lab Overview", ""]
    readme_link = rel(doc.readme, session_dir) if doc.readme.exists() else None
    lab_ref = f"[{doc.number} {doc.name}]({readme_link})" if readme_link else f"{doc.number} {doc.name}"
    lines += [f"**Lab:** {lab_ref}. The lab README is the design: when a device disagrees with it, "
              "the README wins.", ""]
    if doc.objective():
        lines += [doc.objective(), ""]
    if doc.svg:
        lines += ['<p align="center">',
                  f'  <img src="{rel(doc.svg, session_dir)}" alt="{doc.name} topology" width="960">',
                  "</p>", ""]
    inv = doc.inventory()
    if inv:
        lines += ["### Node Inventory", ""] + inv + [""]
    addressing = profile.raw.get("host_addressing") or {}
    if addressing:
        # A gateway is optional: a pure L2 lab (01, 05) gives its desktops an address only.
        hosts = ", ".join(f"{h} `{v['address']}`" + (f" via `{v['gateway']}`" if v.get("gateway") else "")
                          for h, v in addressing.items())
        lines += [f"> [!NOTE]\n> **Harness addition, not part of the lab design.** The export gives these "
                  f"desktops no addressing, so the tool adds static test addresses on import: {hosts}.", ""]

    lines += ["## Scope & Rules", "",
              "- Fix the problem on the devices from their consoles in CML. No wipes, re-imports, "
              "node restarts or cabling changes.",
              "- Save every fix with `write memory`. A fix that is not saved grades **Temporary**.",
              "- Fix the cause, not the symptom. A workaround that makes traffic flow (a static route, "
              "a borrowed address, a shut peer) grades **Temporary**.",
              "- Keep the design. A fix that works but changes the design intent (STP roots, active "
              "gateways, OSPF areas) grades **Off-design**.",
              "- Leave everything else as you found it. Each baseline check that passed at the start "
              "and fails at grading costs 15 points.",
              "- The grader restarts the affected hosts so they ask DHCP again before it tests them.",
              "- Hints unlock in order and cost points; they are logged with a timestamp. "
              f"{TIER_NAMES.get(tier, '')} allows up to h{min(3, {1: 3, 2: 3, 3: 2, 4: 1}.get(tier, 3))}.",
              "- Time from `start` to `grade` is recorded, not scored.", "",
              "| Hints taken | Penalty added | Total penalty |",
              "|-------------|---------------|---------------|"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in HINT_PENALTY_ROWS] + [""]

    lines += ["## Known Good", ""]
    results = (healthy or {}).get("results") or []
    if results:
        run = healthy.get("incident", "")
        lines += [f"What fixed looks like. Every check below passed live on a healthy import of this lab "
                  f"(run `{run}`, {healthy.get('passed', len(results))}/{healthy.get('total', len(results))}). "
                  "The grader reruns the same checks after grading.", "",
                  "| Check | Where | Healthy result | Verify with |",
                  "|-------|-------|----------------|-------------|"]
        lines += [f"| {cell(a)} | {cell(b)} | {cell(c)} | {cell(d)} |" for a, b, c, d in known_good_rows(results)]
        lines += [""]
    else:
        lines += ["The baseline checks in the lab profile, all passing on a healthy import:", ""]
        lines += [f"- `{json.dumps(item)}`" for item in profile.raw.get("baseline") or []] + [""]

    lines += ["## Getting Started", "",
              "Run from Ubuntu (WSL1) with the `breaker` venv active, in `troubleshooting/`.", "",
              "```text",
              f"python -m breaker start {incident}                # import, boot, rebuild; the clock starts",
              f"python -m breaker hint {incident} <fault#>        # next hint for one fault: h1, then h2, then h3",
              f"python -m breaker grade {incident}                # grade live; run it again after more fixes",
              f"python -m breaker debrief {incident}              # reveal the answers, write debrief.md",
              f"python -m breaker clean --id {incident} --remove  # stop, wipe and delete the incident lab",
              "```", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------- debrief


def read_hints(path: Path) -> list[dict[str, Any]]:
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split("\t")
            if len(parts) >= 4:
                out.append({"time": parts[0], "fault": int(parts[1]), "level": parts[2], "text": parts[3]})
    return out


def _minutes_after(start: str | None, when: str) -> str:
    try:
        return f"{(datetime.fromisoformat(when) - datetime.fromisoformat(start)).total_seconds() / 60:.0f} min"
    except (TypeError, ValueError):
        return stamp(when)


QUOTED = re.compile(r"(?<![\w])'([^']{4,}?)'(?![\w])")


def evidence_blocks(text: str) -> list[tuple[str, str]]:
    """[(header, output)] from an evidence file of '## node# command' blocks."""
    blocks = []
    for part in re.split(r"^## ", text, flags=re.M)[1:]:
        header, _, body = part.partition("\n")
        blocks.append((header.strip(), body))
    return blocks


def find_clue(key_clue: str, evidence: list[tuple[str, str]]) -> tuple[str, str] | None:
    """The first evidence line containing a quoted fragment of the key clue:
    (where it came from, the full line as the device printed it)."""
    fragments = [f.strip() for f in QUOTED.findall(key_clue) if f.strip()]
    for frag in fragments:
        rx = re.compile(re.escape(frag) + r"(?![\w]|\.\d)")
        for header, body in evidence:
            for line in body.splitlines():
                if rx.search(line):
                    return header, line.rstrip()
    return None


def _why(rec: dict[str, Any]) -> str:
    steps = {s["question"]: s for s in rec.get("steps") or []}
    grade = rec.get("grade")
    if grade == "Exact":
        return ("Traffic works, the root cause is gone by state, the fix is saved, the design is intact, and "
                "the changed section matches the original line for line.")
    if grade == "Equivalent":
        return ("A valid fix: traffic works, the cause is gone, it is saved and the design is intact, but the "
                f"section differs from the original ({steps.get('original', {}).get('detail', '')}).")
    if grade == "Off-design":
        return ("Traffic works and the fix is saved, but the design intent changed: "
                f"{steps.get('intent', {}).get('detail', '')}.")
    if grade == "Temporary":
        if steps.get("root_cause") and not steps["root_cause"]["ok"]:
            found = "; ".join(w["detail"] for w in rec.get("workarounds") or [])
            return ("Traffic works, but the root cause is still there "
                    f"({steps['root_cause']['detail']})" + (f". Workaround found: {found}." if found else "."))
        return f"Traffic works and the cause is gone, but the fix is not saved ({steps.get('saved', {}).get('detail', '')})."
    if grade == "Partial":
        return f"Only part of the service is back: {steps.get('functional', {}).get('detail', '')} functional checks pass."
    return "No functional check passes: the service is still down at grading time."


def _where_rows(inst: dict[str, Any], device: str) -> list[tuple[str, str]]:
    d = inst.get("debrief") or {}
    features = ", ".join(f"{n} `{h}`" for n, h in inst.get("touched") or [])
    return [
        ("Symptom", inst.get("ticket", "")),
        ("Scope", d.get("scope", "")),
        ("Layer", CATEGORY_LAYER.get(inst.get("category", ""), inst.get("category", ""))),
        ("Device", device),
        ("Feature", features),
        ("Clue", d.get("key_clue", "")),
    ]


def _split_note(command: str) -> tuple[str, str]:
    m = re.match(r"^(.*?)\s*\((.+)\)\s*$", command)
    return (m.group(1).strip(), m.group(2).strip()) if m else (command.strip(), "")


def _method_line(name: str, session_dir: Path) -> str:
    key = METHOD_ALIASES.get(name, name)
    title, use = METHODS.get(key, (key.replace("_", " ").capitalize(), ""))
    page = TOOL_ROOT / "methods" / f"{key}.md"
    ref = f"[{title}]({rel(page, session_dir)})" if page.exists() else f"**{title}**"
    return f"- {ref}: use when {use}." if use else f"- {ref}"


def _fault_section(inst: dict[str, Any], rec: dict[str, Any] | None, hints: list[dict[str, Any]],
                   evidence_dir: Path, session_dir: Path, started: str | None) -> list[str]:
    n = inst["index"]
    d = inst.get("debrief") or {}
    device = inst.get("node") or ""
    if not device:
        try:
            from .faults import by_id
            device = inst["vars"].get(by_id(inst["id"]).node_var, "")
        except Exception:
            device = ""
    out = [f"## Fault {n}: {inst['title']}", ""]
    out += [f"**Ticket:** {inst.get('ticket', '')}", ""]

    out += ["### What was broken", ""]
    nodes = sorted({c["node"] for c in inst.get("changes") or []})
    out += [f"`{inst['id']}` ({inst.get('category', '')}, tier {inst.get('tier', '')}) was injected into the "
            f"imported lab on {', '.join(nodes)}, saved to startup so a reload would not undo it.", ""]
    for c in inst.get("changes") or []:
        removed, added = _diff(c.get("before"), c.get("after"))
        out += _diff_block(c["node"], c["section"], removed, added) + [""]

    out += ["### Your fix", ""]
    if rec is None:
        out += ["Not graded.", ""]
    else:
        out += [f"**{rec['grade']}**, score {rec.get('score', 0)}. {_why(rec)}", "",
                "| Question | Answer | Detail |", "|----------|--------|--------|"]
        out += [f"| {QUESTIONS.get(s['question'], s['question'])} | {'yes' if s['ok'] else '**no**'} | "
                f"{cell(s['detail'])} |" for s in rec.get("steps") or []]
        out += [""]
        changed = [c for c in rec.get("config_now") or [] if c["vs_broken"]["added"] or c["vs_broken"]["removed"]]
        if changed:
            out += ["What you changed in the faulted sections, read from the running config at grading:", ""]
            for c in changed:
                out += _diff_block(c["node"], c["section"], c["vs_broken"]["removed"], c["vs_broken"]["added"]) + [""]
        elif rec.get("config_now") is not None and rec["grade"] in ("Open", "Partial", "Temporary"):
            out += ["The faulted sections were unchanged at grading.", ""]
        left = [c for c in rec.get("config_now") or [] if c["vs_original"]["added"] or c["vs_original"]["removed"]]
        if left and rec["grade"] in ("Equivalent", "Off-design", "Temporary"):
            out += ["Still different from the original lab after your fix (not wrong, but not the design's config "
                    "either; worth tidying on a real network):", ""]
            for c in left:
                out += _diff_block(c["node"], c["section"], c["vs_original"]["removed"], c["vs_original"]["added"]) + [""]
        failing = [r for r in rec.get("functional") or [] if not r["ok"]]
        if failing:
            out += ["Functional checks still failing:", ""]
            out += [f"- `{r['name']}`: {r['detail']}" for r in failing] + [""]
    taken = [h for h in hints if h["fault"] == n]
    if taken:
        out += ["Hints taken: " + "; ".join(f"{h['level']} at {_minutes_after(started, h['time'])} "
                                            f"(\"{h['text']}\")" for h in taken) + ".", ""]

    out += ["### Where to look", "", "| Step | Here |", "|------|------|"]
    out += [f"| {a} | {cell(b)} |" for a, b in _where_rows(inst, device) if b] + [""]

    out += ["### Method", ""]
    out += [_method_line(m, session_dir) for m in d.get("methods") or []] + [""]
    if d.get("reasoning"):
        out += [f"Applied here: {' '.join(str(d['reasoning']).split())}", ""]

    out += ["### Commands", ""]
    shows = (d.get("commands") or {}).get("show") or []
    debugs = (d.get("commands") or {}).get("debug") or []
    if shows:
        out += [f"Show commands{f' (on {device})' if device else ''}:", "", "```text"] + shows + ["```", ""]
    if debugs:
        out += ["Debug, scoped and one at a time, then switch it off:", "", "```text", "terminal monitor"]
        for dbg in debugs:
            cmd, note = _split_note(dbg)
            out += [cmd] + ([f"! {note}"] if note else [])
        out += ["undebug all", "```", ""]
    clue_file = evidence_dir / f"broken-state-fault{n}.txt"
    hit = find_clue(d.get("key_clue", ""), evidence_blocks(clue_file.read_text(encoding="utf-8"))) \
        if clue_file.exists() else None
    out += ["The line that gives it away:", ""]
    if hit:
        header, line = hit
        out += ["```text", f"{header}", line, "```", "",
                f"Quoted from [`evidence/{clue_file.name}`]({rel(clue_file, session_dir)}), captured before the "
                "clock started.", ""]
    else:
        out += [f"{d.get('key_clue', '')}", "",
                "*Not found in this session's broken-state capture; the clue is the one verified when the fault "
                "passed selftest.*", ""]

    if rec is not None and rec["grade"] in ("Temporary", "Off-design", "Partial", "Open"):
        out += ["### Better fix" if rec["grade"] != "Open" else "### Reference fix", ""]
        if rec["grade"] == "Temporary" and d.get("better_fix_if_temporary"):
            out += [str(d["better_fix_if_temporary"]), ""]
        elif rec["grade"] == "Off-design":
            out += ["Put the design back first, then fix the cause where it was injected:", ""]
        out += _fix_block(inst.get("reference_fix") or {}) + [""]
    return out


def render_debrief(session_dir: Path, meta: dict[str, Any], profile: LabProfile,
                   answers: list[dict[str, Any]], grade: dict[str, Any]) -> str:
    doc = LabDoc(profile)
    records = {r["index"]: r for r in grade.get("faults") or []}
    hints = read_hints(session_dir / "hints.log")
    evidence_dir = session_dir / "evidence"
    started = meta.get("started")
    score = grade.get("score", 0)
    grades = [records[i["index"]]["grade"] if i["index"] in records else "Not graded" for i in answers]
    minutes = grade.get("minutes_since_start")
    color = "green" if score >= 85 else "amber" if score >= 60 else "red"
    worst = next((g for g in ("Open", "Partial", "Temporary", "Off-design", "Equivalent", "Exact") if g in grades), "Exact")

    lines = [f"# Debrief {meta['incident']}: {doc.name}", ""]
    lines += [
        badge("Score", f"{score}/100", color),
        badge("Grades", " · ".join(grades), GRADE_COLORS.get(worst, "navy")),
        badge("Tier", TIER_NAMES.get(int(meta["tier"]), meta["tier"]), "amber"),
        badge("Hints", len(hints), "purple"),
        badge("Time", f"{minutes} min" if minutes is not None else "not recorded", "teal"),
        "",
        f"The debrief for [ticket](ticket.md) `{meta['incident']}` on lab {doc.number} {doc.name}: "
        f"{len(answers)} fault{'s' if len(answers) != 1 else ''}, seed {meta['seed']}, graded {stamp(grade.get('graded'))}.",
        "",
        "## Results",
        "",
        "| # | Fault | Grade | Score | Hints | Time |",
        "|---|-------|-------|-------|-------|------|",
    ]
    for inst in answers:
        rec = records.get(inst["index"])
        taken = [h["level"] for h in hints if h["fault"] == inst["index"]]
        lines.append(f"| {inst['index']} | {cell(inst['title'])} | {rec['grade'] if rec else 'Not graded'} | "
                     f"{rec.get('score', 0) if rec else 0} | {', '.join(taken) or 'none'} | "
                     f"{f'{minutes} min' if minutes is not None else ''} |")
    lines += ["", f"Session score **{score}**: the average of the fault scores (100 × grade weight, minus hint "
              "penalties and 15 per collateral failure, never below 0). Time runs from `start` to `grade`; "
              "every fault is graded in the same pass.", ""]

    for inst in answers:
        lines += _fault_section(inst, records.get(inst["index"]), hints, evidence_dir, session_dir, started)

    lines += ["## Collateral", ""]
    lost = grade.get("collateral") or []
    checked = grade.get("collateral_checked", (session_dir / "baseline_before.json").exists())
    if not checked:
        lines += ["Not checked. The session's starting baseline was never recorded (`start` stopped before "
                  "saving it), so there was nothing to compare the lab against at grading.", ""]
    elif lost:
        lines += ["Baseline checks that passed when the session started and failed at grading "
                  "(15 points off each fault):", ""]
        lines += [f"- {x}" for x in lost] + [""]
    else:
        lines += ["None. Every baseline check that passed at the start still passed at grading.", ""]

    lines += ["## Takeaways", ""]
    lines += _takeaways(answers, records, hints, started)
    notes = session_dir / "notes.md"
    if notes.exists() and notes.read_text(encoding="utf-8").strip():
        # The user's own words, verbatim; their headings drop below this section's level.
        own = re.sub(r"^#{1,4}\s+(.+)$", r"**\1**", notes.read_text(encoding="utf-8").strip(), flags=re.M)
        lines += ["### In my own words", "", own, ""]

    lines += ["## Evidence", "", "| File | What it holds |", "|------|---------------|"]
    for name, what in _evidence_rows(session_dir, answers, records):
        lines.append(f"| [`{name}`]({name}) | {cell(what)} |")
    lines += ["", "---", "",
              "*Generated by `python -m breaker debrief` from the live checks in `grade.json`. Whether a fault is "
              "fixed comes from device state read through pyATS; config text is only compared to check that the "
              "fix was saved and to show what changed.*", ""]
    return "\n".join(lines)


def _takeaways(answers, records, hints, started) -> list[str]:
    out = []
    clean = [i for i in answers if not any(h["fault"] == i["index"] for h in hints)
             and records.get(i["index"], {}).get("grade") in ("Exact", "Equivalent")]
    if clean:
        out.append("- Solved without hints: " + ", ".join(f"fault {i['index']} ({i['title']})" for i in clean) + ".")
    for i in answers:
        taken = [h for h in hints if h["fault"] == i["index"]]
        if taken:
            out.append(f"- Fault {i['index']} needed hints up to {taken[-1]['level']}: "
                       + ", ".join(f"{h['level']} at {_minutes_after(started, h['time'])}" for h in taken)
                       + f". The clue to look for next time: {str(i['debrief'].get('key_clue', '')).rstrip('.')}.")
    for i in answers:
        rec = records.get(i["index"])
        if rec and rec["grade"] not in ("Exact", "Equivalent"):
            out.append(f"- Fault {i['index']} graded {rec['grade']}: {_why(rec)}")
    methods: dict[str, list[int]] = {}
    for i in answers:
        for m in (i.get("debrief") or {}).get("methods") or []:
            methods.setdefault(METHOD_ALIASES.get(m, m), []).append(i["index"])
    shared = [(m, idx) for m, idx in methods.items() if len(idx) > 1]
    if shared:
        out.append("- Methods that cracked more than one fault: " + "; ".join(
            f"{METHODS.get(m, (m,))[0].lower()} (faults {', '.join(map(str, idx))})" for m, idx in shared) + ".")
    return (out or ["- One fault, solved cleanly."]) + [""]


def _evidence_rows(session_dir: Path, answers, records) -> list[tuple[str, str]]:
    rows = []
    fixed = [("ticket.md", "The ticket as issued"),
             ("broken.yaml", "The lab exactly as imported, faults included (sanitized)"),
             ("answers.json", "Every fault instance: target, injected change, checks, reference fix"),
             ("grade.json", "Raw check results for every fault, plus the baseline rerun for collateral"),
             ("baseline_before.json", "The baseline at the start of the session (collateral is judged against it)"),
             ("hints.log", "Hints taken, timestamped")]
    rows += [(name, what) for name, what in fixed if (session_dir / name).exists()]
    ev = session_dir / "evidence"
    if ev.exists():
        names = {}
        for rec in records.values():
            for part in ("functional", "root_cause", "intent", "workarounds"):
                for k, res in enumerate(rec.get(part) or [], 1):
                    names[f"fault{rec['index']}-{part}-{k}.txt"] = (
                        f"Fault {rec['index']} {part.replace('_', ' ')}: {res['name']}, "
                        f"{'PASS' if res['ok'] else 'FAIL'} ({res['detail']})")
        for path in sorted(ev.glob("*.txt")):
            m = re.match(r"broken-state-fault(\d+)\.txt", path.name)
            what = (f"Fault {m.group(1)} show commands in the broken state, captured at start" if m
                    else names.get(path.name, "Captured output"))
            rows.append((f"evidence/{path.name}", what))
    return rows


# --------------------------------------------------------------------------- sanitizer


def sanitize_text(text: str) -> tuple[str, int]:
    """Banner blocks and EULA lines out of captured output; returns (text, lines removed)."""
    stripped = strip_banners(text)
    kept = [line for line in stripped.split("\n") if not EULA_RE.search(line)]
    out = "\n".join(kept)
    return out, text.count("\n") - out.count("\n")


def _sanitize_json(obj: Any) -> tuple[Any, int]:
    if isinstance(obj, str):
        return sanitize_text(obj) if ("banner" in obj or EULA_RE.search(obj)) else (obj, 0)
    if isinstance(obj, list):
        total, out = 0, []
        for x in obj:
            y, n = _sanitize_json(x)
            out.append(y)
            total += n
        return out, total
    if isinstance(obj, dict):
        total, out = 0, {}
        for k, v in obj.items():
            y, n = _sanitize_json(v)
            out[k] = y
            total += n
        return out, total
    return obj, 0


def sanitize_session(session_dir: Path) -> list[str]:
    """Clean every file that gets published, in place. Returns what changed."""
    changed = []
    broken = session_dir / "broken.yaml"
    if broken.exists():
        model = LabModel.load(broken)
        issues = model.sanitize_issues()
        if issues:
            for node in model.nodes:
                for f in node.raw.get("configuration") or []:
                    if f.get("content"):
                        f["content"], _ = sanitize_text(f["content"])
            broken.write_text(model.to_yaml(), encoding="utf-8")
            changed.append(f"broken.yaml: {', '.join(issues)}")
    for name in ("grade.json", "baseline_before.json", "answers.json"):
        path = session_dir / name
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue  # sealed (base64): never published
        clean, n = _sanitize_json(data)
        if n:
            path.write_text(json.dumps(clean, indent=2), encoding="utf-8")
            changed.append(f"{name}: {n} line(s)")
    for path in sorted((session_dir / "evidence").glob("*.txt")) if (session_dir / "evidence").exists() else []:
        text = path.read_text(encoding="utf-8")
        clean, n = sanitize_text(text)
        if clean != text:
            path.write_text(clean, encoding="utf-8")
            changed.append(f"evidence/{path.name}: {n} line(s)")
    return changed


def publish_issues(session_dir: Path, files: list[Path]) -> list[str]:
    """Anything banner- or EULA-like left in the files about to be published."""
    from .lab_model import sanitize_issues

    out = []
    for path in files:
        if path.suffix in (".md", ".txt", ".json", ".yaml", ".log") and path.exists():
            for issue in sanitize_issues(path.read_text(encoding="utf-8")):
                out.append(f"{rel(path, session_dir)}: {issue}")
    return out


# --------------------------------------------------------------------------- session index


INDEX_HEADER = ["| Date | Session | Lab | Tier | Faults | Score | Grades |",
                "|------|---------|-----|------|--------|-------|--------|"]


def index_row(session_dir: Path, meta: dict[str, Any], doc_name: str, grade: dict[str, Any]) -> str:
    grades = ", ".join(r["grade"] for r in grade.get("faults") or [])
    link = f"sessions/{session_dir.name}/debrief.md"
    return (f"| {meta['session'][:10]} | [{meta['incident']}]({link}) | {meta['lab'][:2]} {doc_name} | "
            f"T{meta['tier']} | {meta['faults']} | {grade.get('score', 0)} | {grades} |")


def update_index(readme: Path, row: str, session_name: str) -> bool:
    """Insert or replace this session's row in the README's Session Index."""
    text = readme.read_text(encoding="utf-8")
    m = re.search(r"^## Session Index[ \t]*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    if not m:
        return False
    rows = [line for line in m.group(1).splitlines() if line.startswith("| ") and "](sessions/" in line]
    rows = [r for r in rows if f"sessions/{session_name}/" not in r] + [row]
    rows.sort(key=lambda r: r.split("|")[1].strip() + r.split("|")[2], reverse=True)  # newest first
    body = "\n\nPublished break-fix sessions, newest first. Each links to its debrief.\n\n" + \
           "\n".join(INDEX_HEADER + rows) + "\n\n"
    readme.write_text(text[:m.start(1)] + body + text[m.end(1):].lstrip("\n"), encoding="utf-8")
    return True
