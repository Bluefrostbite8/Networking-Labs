"""Command line: python -m breaker <command>.

Build step 2 commands: list, show, doctor, start --healthy, rebuild, clean.
break, hint, grade, debrief and selftest arrive in later build steps.
"""

from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime

from . import cml_api
from .lab_model import TOOL_ROOT, LabModel, LabModelError, LabProfile, lab_dirs, resolve_lab

FAULTS_DIR = TOOL_ROOT / "faults"


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
    # Short id keeps the CML title under its length limit: "Switching [incident 03-healthy-1622]"
    incident_id = args.id or f"{profile.lab[:2]}-healthy-{datetime.now():%H%M}"
    cfg = cml_api.load_config()
    client = cml_api.connect()

    needed = model.licensed_node_count()
    cml_api.check_capacity(client, needed, cfg["node_cap"])
    log(f"capacity ok: {needed} nodes needed, cap {cfg['node_cap']}")

    lab = cml_api.import_lab(client, model, incident_id, log)
    print(f"\nIf anything below fails, clean up with:\n  python -m breaker clean --id {incident_id}\n", flush=True)

    boot = profile.boot
    cml_api.boot(lab, boot["timeout_min"], log)
    consoles = cml_api.Consoles(lab)
    try:
        ios = [n.label for n in model.ios_nodes() if n.label not in profile.never_touch]
        cml_api.wait_consoles(consoles, ios, boot["ready_check"], boot["timeout_min"], log)
        cml_api.rebuild(lab, consoles, model, profile, log)
    finally:
        consoles.close()
    log(f"healthy lab ready in {(time.monotonic() - t0) / 60:.1f} min: {lab.title} (id {lab.id})")
    return 0


def cmd_rebuild(args) -> int:
    """Rerun the rebuild on an incident lab that is already running (no import, no boot)."""
    t0 = time.monotonic()
    log = cml_api.default_log
    profile = LabProfile.load(args.lab)
    model = profile.model()
    client = cml_api.connect()
    labs = cml_api.find_incident_labs(client, args.id)
    if len(labs) != 1:
        print(f"error: found {len(labs)} incident labs with id {args.id}", file=sys.stderr)
        return 1
    lab = labs[0]
    title = cml_api.server_title(lab)
    if not title.startswith(model.title + " ["):
        print(f"error: {title!r} is not a {profile.lab} lab", file=sys.stderr)
        return 1
    log(f"rebuilding {title} (id {lab.id})")
    consoles = cml_api.Consoles(lab)
    try:
        ios = [n.label for n in model.ios_nodes() if n.label not in profile.never_touch]
        cml_api.wait_consoles(consoles, ios, profile.boot["ready_check"], profile.boot["timeout_min"], log)
        cml_api.rebuild(lab, consoles, model, profile, log)
    finally:
        consoles.close()
    log(f"rebuild done in {(time.monotonic() - t0) / 60:.1f} min: {title}")
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

    s = sub.add_parser("rebuild", help="rerun the VLAN/VTP rebuild on a running incident lab")
    s.add_argument("lab", help="lab folder or prefix, e.g. 03")
    s.add_argument("--id", required=True, help="incident id of the running lab, e.g. 03-healthy-1735")

    s = sub.add_parser("clean", help="stop and wipe incident labs only")
    s.add_argument("--id", help="only the lab with this incident id")
    s.add_argument("--remove", action="store_true", help="also delete the lab from the server")
    s.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    return p


COMMANDS = {"list": cmd_list, "show": cmd_show, "doctor": cmd_doctor, "start": cmd_start,
            "rebuild": cmd_rebuild, "clean": cmd_clean}


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
