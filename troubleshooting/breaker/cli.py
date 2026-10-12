"""Command line: python -m breaker <command>.

Build steps 2 and 3: list, show, doctor, start --healthy, rebuild, baseline, clean.
break, hint, grade, debrief and selftest arrive in later build steps.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import traceback
from datetime import datetime

from . import cml_api
from .lab_model import TOOL_ROOT, LabModel, LabModelError, LabProfile, lab_dirs, resolve_lab

FAULTS_DIR = TOOL_ROOT / "faults"
RUNS_DIR = TOOL_ROOT / "runs"  # local evidence from healthy runs (gitignored)


def cmd_list(args) -> int:
    faults = len(list(FAULTS_DIR.glob("*/*.yaml"))) if FAULTS_DIR.exists() else 0
    print(f"{'Lab':42} {'CML title':10} {'Nodes':>5} {'Licensed':>8}  {'Profile':8} Faults")
    for d in lab_dirs():
        model = LabModel.load(d / "topology.yaml")
        has_profile = (TOOL_ROOT / "labs" / f"{d.name}.yaml").exists()
        # Per-lab fault matching (applies_when) arrives with checks.py in build step 4.
        print(f"{d.name:42} {model.title:10} {len(model.nodes):>5} {model.licensed_node_count():>8}  "
              f"{'yes' if has_profile else 'no':8} {faults}")
    return 0


def cmd_show(args) -> int:
    folder = resolve_lab(args.lab)
    model = LabModel.load(folder / "topology.yaml")
    print(f"{folder.name}: '{model.title}', {len(model.nodes)} nodes, {len(model.links)} links, "
          f"{model.licensed_node_count()} count against the licence\n")
    print(f"{'Node':10} {'Definition':18} {'Config file':16} Features")
    for n in model.nodes:
        cfg = n.config
        feats = ", ".join(sorted(cfg.features())) if cfg else ("never touched" if n.is_infra else "host")
        print(f"{n.label:10} {n.node_definition:18} {n.config_name or '-':16} {feats}")
    print(f"\n{'Link':5} {'A side':30} B side")
    for link in model.links:
        print(f"{link.id:5} {str(link.a):30} {link.b}")
    issues = model.sanitize_issues()
    print("\nSanitizer: " + ("clean" if not issues else f"ISSUES {issues}"))
    try:
        LabProfile.load(folder.name).validate(model)
        print("Profile: present and valid")
    except LabModelError as exc:
        print(f"Profile: {exc}")
    return 0


def cmd_doctor(args) -> int:
    env = cml_api.credentials()
    print(f"VIRL2_URL      {env['VIRL2_URL']}")
    print(f"CML_CA_BUNDLE  {env['CML_CA_BUNDLE']} (TLS verified)")
    cfg = cml_api.load_config()
    client = cml_api.connect()
    print(f"login          ok as {env['VIRL2_USER']}")
    usage = cml_api.node_usage(client)
    running = sum(u.running for u in usage)
    print(f"node cap       {running} running of {cfg['node_cap']} (config.yaml)")
    for u in sorted(usage, key=lambda u: -u.running):
        tag = "  incident" if cml_api.incident_id_of(u.title) else ""
        print(f"  {u.running:>3}  {u.title}  (id {u.id}){tag}")
    return 0


def _consoles(lab, model, profile):
    hosts = {n.label for n in model.nodes if n.is_host}
    return cml_api.Consoles(lab, hosts=hosts)


def _wait_ready(consoles, model, profile, log=cml_api.default_log, incident_id: str = "run",
                labels: list[str] | None = None, skip: tuple[str, ...] = ()) -> None:
    if labels is None:
        labels = [n.label for n in model.console_nodes(profile.never_touch) if n.label not in skip]

    def save_console(label: str, text: str) -> None:
        RUNS_DIR.mkdir(exist_ok=True)
        path = RUNS_DIR / f"{incident_id}-console-{label.strip()}.txt"
        path.write_text(text, encoding="utf-8")
        log(f"saved {label.strip()}'s console output to {path.relative_to(TOOL_ROOT)}")

    cml_api.wait_consoles(consoles, labels, profile.boot["ready_check"], profile.boot["timeout_min"], log,
                          on_fail=save_console)


def _write_result(incident_id: str, data: dict) -> None:
    """One small JSON per run, success or failure, so results can be read later."""
    RUNS_DIR.mkdir(exist_ok=True)
    (RUNS_DIR / f"{incident_id}-result.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def cmd_start(args) -> int:
    if not args.healthy:
        print("start needs --healthy until 'break' exists (build step 4).", file=sys.stderr)
        return 2
    t0 = time.monotonic()
    log = cml_api.default_log
    profile = LabProfile.load(args.lab)
    model = profile.model()
    issues = model.sanitize_issues()
    if issues:
        log(f"warning: sanitizer found {sum(map(len, issues.values()))} issue(s) in {', '.join(issues)}")
    addressing = profile.raw.get("host_addressing") or {}
    if addressing:  # only the imported copy changes; topology.yaml in the repo stays as it is
        model.apply_host_addressing(addressing)
        log(f"harness test addresses on {', '.join(addressing)} (not part of the lab design)")
    # Short id keeps the CML title under its length limit: "Switching [incident 03-healthy-1622]"
    incident_id = args.id or f"{profile.lab[:2]}-healthy-{datetime.now():%H%M}"
    RUNS_DIR.mkdir(exist_ok=True)
    cml_api.LOG_FILE = RUNS_DIR / f"{incident_id}.log"  # the tool's own log: no tee needed
    cfg = cml_api.load_config()
    client = cml_api.connect()
    result = {"lab": profile.lab, "incident": incident_id, "started": datetime.now().isoformat(timespec="seconds"),
              "stage": "capacity", "ok": False}

    lab = None
    try:
        needed = model.licensed_node_count()
        cml_api.check_capacity(client, needed, cfg["node_cap"])
        log(f"capacity ok: {needed} nodes needed, cap {cfg['node_cap']}")

        result["stage"] = "import"
        lab = cml_api.import_lab(client, model, incident_id, log)
        if not args.clean_after:
            print(f"\nIf anything below fails, clean up with:\n  python -m breaker clean --id {incident_id}\n",
                  flush=True)

        result["stage"] = "boot"
        cml_api.boot(lab, profile.boot["timeout_min"], log)
        consoles = _consoles(lab, model, profile)
        try:
            # A node that needs a power cycle before its console works (the 9800-CL)
            # is left out of the first wait and brought up after the rebuild, which
            # creates the VLANs its readiness ping travels over.
            cycle = profile.boot.get("console_power_cycle")
            skip = (cycle["node"],) if cycle else ()
            result["stage"] = "consoles"
            _wait_ready(consoles, model, profile, log, incident_id, skip=skip)
            result["stage"] = "rebuild"
            cml_api.rebuild(lab, consoles, model, profile, log)
            if cycle:
                result["stage"] = "power cycle"
                cml_api.console_power_cycle(lab, consoles, cycle, profile.boot["timeout_min"], log)
                result["stage"] = "consoles"
                _wait_ready(consoles, model, profile, log, incident_id, labels=[cycle["node"]])
            result["stage"] = "baseline"
            ok, summary = run_baseline(lab, consoles, model, profile, incident_id)
            result.update(summary)
        finally:
            consoles.close()
        result["ok"] = ok
        result["stage"] = "done"
        state = "healthy lab ready" if ok else "lab up, but the baseline did not fully pass"
        log(f"{state} in {(time.monotonic() - t0) / 60:.1f} min: {lab.title} (id {lab.id})")
        return 0 if ok else 1
    except (cml_api.CmlError, LabModelError) as exc:
        result["error"] = str(exc)
        raise
    except Exception as exc:  # unexpected: keep the reason for the run file, then let it surface
        result["error"] = f"{type(exc).__name__}: {exc}"
        raise
    except KeyboardInterrupt:
        result["error"] = "interrupted (Ctrl+C)"
        raise
    finally:
        result["minutes"] = round((time.monotonic() - t0) / 60, 1)
        _write_result(incident_id, result)
        if args.clean_after and lab is not None:
            try:
                cml_api.stop_and_wipe(lab, remove=True, log=log)
            except Exception as exc:
                log(f"clean-after failed ({exc}); run: python -m breaker clean --id {incident_id} --remove")


def cmd_rebuild(args) -> int:
    """Rerun the rebuild on an incident lab that is already running (no import, no boot)."""
    t0 = time.monotonic()
    log = cml_api.default_log
    profile, model, lab, title = _attach(args)
    RUNS_DIR.mkdir(exist_ok=True)
    cml_api.LOG_FILE = RUNS_DIR / f"{args.id}.log"
    log(f"rebuilding {title} (id {lab.id})")
    consoles = _consoles(lab, model, profile)
    try:
        _wait_ready(consoles, model, profile, log, args.id)
        cml_api.rebuild(lab, consoles, model, profile, log)
    finally:
        consoles.close()
    log(f"rebuild done in {(time.monotonic() - t0) / 60:.1f} min: {title}")
    return 0


def run_baseline(lab, consoles, model, profile, incident_id: str) -> tuple[bool, dict]:
    """Run the profile baseline live, print a table, save evidence.
    Passes when every required check passes; optional checks are reported only."""
    from .checks import Checker

    log = cml_api.default_log
    if not profile.raw.get("baseline"):
        log("no baseline in the profile yet")
        return False, {"passed": 0, "total": 0}
    log("running baseline checks")
    checker = Checker(lambda node, cmd: consoles.run(node, cmd), model, profile)
    results = checker.baseline()
    width = max(len(r.name) for r in results)
    for r in results:
        tag = ("PASS" if r.ok else "FAIL") if not r.optional else ("ok  " if r.ok else "info")
        print(f"  {tag}  {r.name:<{width}}  {r.detail}{'  (optional)' if r.optional else ''}", flush=True)
    required = [r for r in results if not r.optional]
    passed = sum(r.ok for r in required)
    summary = {"passed": passed, "total": len(required),
               "failed": [f"{r.name}: {r.detail}" for r in required if not r.ok],
               "optional_failed": [f"{r.name}: {r.detail}" for r in results if r.optional and not r.ok]}
    RUNS_DIR.mkdir(exist_ok=True)
    path = RUNS_DIR / f"{incident_id}-baseline.json"
    path.write_text(json.dumps({"lab": profile.lab, "incident": incident_id, **summary,
                                "results": [r.as_dict() for r in results]}, indent=2), encoding="utf-8")
    log(f"baseline {passed}/{len(required)} required checks passed; evidence in {path.relative_to(TOOL_ROOT)}")
    return passed == len(required), summary


def _attach(args):
    """The one running incident lab for this profile and id."""
    profile = LabProfile.load(args.lab)
    model = profile.model()
    client = cml_api.connect()
    labs = cml_api.find_incident_labs(client, args.id)
    if len(labs) != 1:
        raise cml_api.CmlError(f"found {len(labs)} incident labs with id {args.id}")
    lab = labs[0]
    title = cml_api.server_title(lab)
    if not title.startswith(model.title + " ["):
        raise cml_api.CmlError(f"{title!r} is not a {profile.lab} lab")
    return profile, model, lab, title


def cmd_baseline(args) -> int:
    profile, model, lab, title = _attach(args)
    RUNS_DIR.mkdir(exist_ok=True)
    cml_api.LOG_FILE = RUNS_DIR / f"{args.id}.log"
    cml_api.default_log(f"baseline on {title} (id {lab.id})")
    consoles = _consoles(lab, model, profile)
    try:
        _wait_ready(consoles, model, profile, incident_id=args.id)
        ok, _ = run_baseline(lab, consoles, model, profile, args.id)
    finally:
        consoles.close()
    return 0 if ok else 1


def cmd_console(args) -> int:
    """Print and save CML's console buffer for one node (no pyATS, nothing sent to the node)."""
    profile, model, lab, title = _attach(args)
    node = lab.get_node_by_label(args.node)
    raw = node.console_logs(0, lines=args.lines)
    text = raw.get("log", "") if isinstance(raw, dict) else ("\n".join(map(str, raw)) if isinstance(raw, list) else str(raw))
    RUNS_DIR.mkdir(exist_ok=True)
    path = RUNS_DIR / f"{args.id}-console-{args.node.strip()}.txt"
    path.write_text(text, encoding="utf-8")
    print(text if text.strip() else "(console buffer is empty: the node has printed nothing)")
    print(f"\nnode state: {node.state}; saved to {path.relative_to(TOOL_ROOT)}")
    return 0


def cmd_clean(args) -> int:
    client = cml_api.connect()
    labs = cml_api.find_incident_labs(client, args.id)
    if not labs:
        print("No incident labs found" + (f" for id {args.id}" if args.id else "") + ". Nothing touched.")
        return 0
    action = "stop, wipe and DELETE" if args.remove else "stop and wipe"
    print(f"Will {action} {len(labs)} incident lab(s):")
    for lab in labs:
        print(f"  {cml_api.server_title(lab)}  (id {lab.id})")
    if not args.yes and input("Type yes to continue: ").strip().lower() != "yes":
        print("Cancelled. Nothing touched.")
        return 1
    for lab in labs:
        cml_api.stop_and_wipe(lab, remove=args.remove)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="breaker", description="CML break-fix troubleshooting tool")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="the six labs, node counts, profiles and fault count")

    s = sub.add_parser("show", help="parsed view of one lab: nodes, features, cabling")
    s.add_argument("lab", help="lab folder or prefix, e.g. 03")

    sub.add_parser("doctor", help="check credentials, TLS, login and running-node headroom")

    s = sub.add_parser("start", help="import and boot a lab, then rebuild VLANs/VTP")
    s.add_argument("lab", help="lab folder or prefix, e.g. 03")
    s.add_argument("--healthy", action="store_true", help="import the unbroken topology.yaml")
    s.add_argument("--id", help="incident id for the title suffix (default: <lab#>-healthy-HHMM)")
    s.add_argument("--clean-after", action="store_true",
                   help="stop, wipe and delete the lab when done (pass or fail); results stay in runs/")

    s = sub.add_parser("rebuild", help="rerun the VLAN/VTP rebuild on a running incident lab")
    s.add_argument("lab", help="lab folder or prefix, e.g. 03")
    s.add_argument("--id", required=True, help="incident id of the running lab, e.g. 03-healthy-1735")

    s = sub.add_parser("baseline", help="run the profile's baseline checks on a running incident lab")
    s.add_argument("lab", help="lab folder or prefix, e.g. 03")
    s.add_argument("--id", required=True, help="incident id of the running lab, e.g. 03-healthy-1735")

    s = sub.add_parser("console", help="show CML's console buffer for one node of a running incident lab")
    s.add_argument("lab", help="lab folder or prefix, e.g. 06")
    s.add_argument("node", help="node label, e.g. R2")
    s.add_argument("--id", required=True, help="incident id of the running lab")
    s.add_argument("--lines", type=int, default=80, help="how many lines (default 80)")

    s = sub.add_parser("clean", help="stop and wipe incident labs only")
    s.add_argument("--id", help="only the lab with this incident id")
    s.add_argument("--remove", action="store_true", help="also delete the lab from the server")
    s.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    return p


COMMANDS = {"list": cmd_list, "show": cmd_show, "doctor": cmd_doctor, "start": cmd_start,
            "rebuild": cmd_rebuild, "baseline": cmd_baseline, "console": cmd_console,
            "clean": cmd_clean}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (cml_api.CmlError, LabModelError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        # Show where it was waiting, so a hang can be diagnosed from the output.
        print("\ninterrupted while in:", file=sys.stderr)
        traceback.print_exc(limit=-8, file=sys.stderr)
        return 130
