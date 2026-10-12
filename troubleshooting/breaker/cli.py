"""Command line: python -m breaker <command>.

Steps 2 and 3: list, show, doctor, start --healthy, rebuild, baseline, console, clean.
Step 4: break, start <session>, hint, grade, selftest.
Step 5: debrief, and the full ticket (report.py).
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
    from .faults import library

    lib = library() if FAULTS_DIR.exists() else []
    print(f"{'Lab':42} {'CML title':10} {'Nodes':>5} {'Licensed':>8}  {'Profile':8} Faults (of {len(lib)})")
    for d in lab_dirs():
        model = LabModel.load(d / "topology.yaml")
        path = TOOL_ROOT / "labs" / f"{d.name}.yaml"
        fits = "-"
        if path.exists():
            profile = LabProfile.load(d.name)
            fit = [f for f in lib if f.candidates(model, profile)]
            fits = f"{sum(f.proven_on(d.name) for f in fit)} proven, {len(fit)} fit"
        print(f"{d.name:42} {model.title:10} {len(model.nodes):>5} {model.licensed_node_count():>8}  "
              f"{'yes' if path.exists() else 'no':8} {fits}")
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
    import re

    hosts = {n.label for n in model.nodes if n.is_host}
    hostnames = {}
    for n in model.nodes:
        cfg = n.config if n.is_ios else None
        m = re.search(r"^hostname (\S+)\s*$", cfg.text, re.M) if cfg else None
        if m:
            hostnames[n.label] = m.group(1)
    return cml_api.Consoles(lab, hosts=hosts, hostnames=hostnames)


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


def bring_up(profile: LabProfile, model: LabModel, incident_id: str, state: dict, log, strict: bool = True):
    """Capacity check, import, boot, consoles, rebuild. Sets state["lab"] as soon as
    the lab exists, so a caller can clean up whatever stage fails."""
    cfg = cml_api.load_config()
    client = cml_api.connect()
    state["stage"] = "capacity"
    needed = model.licensed_node_count()
    cml_api.check_capacity(client, needed, cfg["node_cap"])
    log(f"capacity ok: {needed} nodes needed, cap {cfg['node_cap']}")
    state["stage"] = "import"
    lab = cml_api.import_lab(client, model, incident_id, log)
    state["lab"] = lab
    state["stage"] = "boot"
    cml_api.boot(lab, profile.boot["timeout_min"], log)
    consoles = _consoles(lab, model, profile)
    state["consoles"] = consoles
    # A node that needs a power cycle before its console works (the 9800-CL) is
    # left out of the first wait and brought up after the rebuild, which creates
    # the VLANs its readiness ping travels over.
    cycle = profile.boot.get("console_power_cycle")
    skip = (cycle["node"],) if cycle else ()
    state["stage"] = "consoles"
    _wait_ready(consoles, model, profile, log, incident_id, skip=skip)
    state["stage"] = "rebuild"
    cml_api.rebuild(lab, consoles, model, profile, log, strict=strict)
    if cycle:
        state["stage"] = "power cycle"
        cml_api.console_power_cycle(lab, consoles, cycle, profile.boot["timeout_min"], log)
        state["stage"] = "consoles"
        _wait_ready(consoles, model, profile, log, incident_id, labels=[cycle["node"]])
    return lab, consoles


def _with_test_addresses(profile: LabProfile, model: LabModel, log) -> LabModel:
    addressing = profile.raw.get("host_addressing") or {}
    if addressing:  # only the imported copy changes; topology.yaml in the repo stays as it is
        model.apply_host_addressing(addressing)
        log(f"harness test addresses on {', '.join(addressing)} (not part of the lab design)")
    return model


def cmd_start(args) -> int:
    if not args.healthy:
        return start_session(args)
    t0 = time.monotonic()
    log = cml_api.default_log
    profile = LabProfile.load(args.lab)
    model = profile.model()
    issues = model.sanitize_issues()
    if issues:
        log(f"warning: sanitizer found {sum(map(len, issues.values()))} issue(s) in {', '.join(issues)}")
    _with_test_addresses(profile, model, log)
    # Short id keeps the CML title under its length limit: "Switching [incident 03-healthy-1622]"
    incident_id = args.id or f"{profile.lab[:2]}-healthy-{datetime.now():%H%M}"
    RUNS_DIR.mkdir(exist_ok=True)
    cml_api.LOG_FILE = RUNS_DIR / f"{incident_id}.log"  # the tool's own log: no tee needed
    result = {"lab": profile.lab, "incident": incident_id, "started": datetime.now().isoformat(timespec="seconds"),
              "stage": "capacity", "ok": False}
    state: dict = {}
    try:
        if not args.clean_after:
            print(f"\nIf anything below fails, clean up with:\n  python -m breaker clean --id {incident_id}\n",
                  flush=True)
        lab, consoles = bring_up(profile, model, incident_id, state, log)
        try:
            state["stage"] = "baseline"
            ok, summary = run_baseline(lab, consoles, model, profile, incident_id)
            result.update(summary)
        finally:
            consoles.close()
        result["ok"] = ok
        state["stage"] = "done"
        verdict = "healthy lab ready" if ok else "lab up, but the baseline did not fully pass"
        log(f"{verdict} in {(time.monotonic() - t0) / 60:.1f} min: {lab.title} (id {lab.id})")
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
        result["stage"] = state.get("stage", "capacity")
        result["minutes"] = round((time.monotonic() - t0) / 60, 1)
        _write_result(incident_id, result)
        if args.clean_after and state.get("lab") is not None:
            try:
                cml_api.stop_and_wipe(state["lab"], remove=True, log=log)
            except Exception as exc:
                log(f"clean-after failed ({exc}); run: python -m breaker clean --id {incident_id} --remove")


# --------------------------------------------------------------------------- sessions (step 4)


def _healthy_baseline(lab: str) -> dict | None:
    """Engine rule 7: the healthy lab's baseline must have passed once before any
    injection. Returns the newest passing healthy run (its results feed the
    ticket's Known Good table), or None."""
    for path in sorted(RUNS_DIR.glob("*-baseline.json"), reverse=True):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get("lab") == lab and data.get("total") and data.get("passed") == data.get("total") \
                and "-healthy-" in str(data.get("incident", "")):
            return data
    return None


def _healthy_baseline_passed(lab: str) -> str | None:
    data = _healthy_baseline(lab)
    return data["incident"] if data else None


def _ticket(profile, model, instances, tier: int, seed: int, healthy: dict | None):
    from .report import render_ticket

    return lambda incident, path: render_ticket(profile, model, instances, tier, seed, incident, path,
                                                healthy=healthy)


def cmd_break(args) -> int:
    import secrets

    from .faults import pick
    from .session import Session

    profile = LabProfile.load(args.lab)
    model = profile.model()
    healthy = _healthy_baseline(profile.lab)
    proof = healthy["incident"] if healthy else None
    if not proof:
        raise LabModelError(f"no passing healthy baseline for {profile.lab} in runs/; "
                            f"run: python -m breaker start {args.lab} --healthy (engine rule 7)")
    seed = args.seed if args.seed is not None else 1000 + secrets.randbelow(9000)
    broken, instances = pick(model, profile, args.tier, seed, only=args.fault)
    session = Session.create(profile, broken, instances, args.tier, seed,
                             _ticket(profile, model, instances, args.tier, seed, healthy))
    session.update(healthy_baseline=proof)
    print(f"Session {session.path.name}: tier T{args.tier}, {len(instances)} fault(s), seed {seed}")
    print(f"Ticket:  {session.path.relative_to(TOOL_ROOT)}/ticket.md")
    print(f"Next:    python -m breaker start {session.incident}")
    return 0


def start_session(args, session=None, state: dict | None = None):
    """Import the broken lab, rebuild, record the baseline the session starts from.
    The clock starts here. Returns 0, or (lab, consoles) when called by selftest."""
    from .checks import Checker
    from .session import Session

    t0 = time.monotonic()
    log = cml_api.default_log
    session = session or Session.find(args.lab)
    profile = session.profile()
    model = _with_test_addresses(profile, session.broken_model(), log)
    cml_api.LOG_FILE = session.path / "session.log"
    state = state if state is not None else {}
    keep_open = state.get("keep_open", False)
    print(f"\nIf anything below fails, clean up with:\n  python -m breaker clean --id {session.incident}\n", flush=True)
    lab, consoles = bring_up(profile, model, session.incident, state, log, strict=False)
    try:
        # Runtime faults would be pushed and saved here; the first five are all static.
        state["stage"] = "broken state"
        capture_broken_state(session, consoles, log)
        state["stage"] = "baseline before"
        log("recording the baseline the session starts from (collateral is judged against it)")
        checker = Checker(lambda node, cmd: consoles.run(node, cmd), _with_test_addresses(
            profile, profile.model(), lambda _m: None), profile)
        checker.skip_stale_leases = True
        before = checker.baseline()
        session.write_json("baseline_before.json", [r.as_dict() for r in before])
        failing = [r for r in before if not r.ok and not r.optional]
        log(f"session baseline: {len(before) - len(failing)}/{len(before)} pass (the failures are the symptoms)")
        session.update(started=datetime.now().isoformat(timespec="seconds"), lab_id=lab.id)
    except BaseException:
        consoles.close()
        raise
    if keep_open:
        return lab, consoles
    consoles.close()
    log(f"session {session.incident} ready in {(time.monotonic() - t0) / 60:.1f} min. The clock is running.")
    print(f"Ticket: {session.path.relative_to(TOOL_ROOT)}/ticket.md")
    return 0


def capture_broken_state(session, consoles, log) -> None:
    """Before the clock: each fault's own show commands on the node it changed,
    saved to evidence/broken-state-fault<N>.txt. The debrief quotes the key clue
    from these files."""
    from .faults import by_id

    ev = session.path / "evidence"
    ev.mkdir(exist_ok=True)
    for inst in session.answers():
        node = inst.get("node")
        if not node:  # sessions broken before build step 5
            node = inst["vars"].get(by_id(inst["id"]).node_var)
        blocks = []
        for cmd in (inst["debrief"].get("commands") or {}).get("show") or []:
            try:
                out = consoles.run(node, cmd)
            except Exception as exc:  # evidence only: never stop the session for it
                out = f"(failed: {type(exc).__name__}: {exc})"
            blocks.append(f"## {node}# {cmd}\n{out.rstrip()}")
        path = ev / f"broken-state-fault{inst['index']}.txt"
        path.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    log(f"broken-state evidence saved for {len(session.answers())} fault(s) in evidence/")


def cmd_hint(args) -> int:
    from .session import Session

    session = Session.find(args.session)
    level, text = session.take_hint(args.fault)
    print(f"Fault {args.fault}, hint h{level}: {text}")
    return 0


def grade_session(session, lab, consoles, log, save: bool = True, collateral_check: bool = True) -> dict:
    from .checks import Checker
    from .grading import Grader, collateral, evidence_files, score, stamp

    profile = session.profile()
    model = _with_test_addresses(profile, profile.model(), lambda _m: None)
    checker = Checker(lambda node, cmd: consoles.run(node, cmd), model, profile)
    checker.skip_stale_leases = True
    grader = Grader(checker, lambda node, cmd: consoles.run(node, cmd),
                    lambda hosts: cml_api.restart_hosts(lab, consoles, hosts, log), log)
    answers = session.answers()
    records = []
    for inst in answers:
        log(f"grading fault {inst['index']}")
        records.append(grader.grade_fault(inst))
    after, lost = [], []
    if collateral_check:  # selftest skips it: only each fault's grade matters there
        log("rerunning the lab baseline for collateral")
        checker.fresh()
        after = checker.baseline()
        lost = collateral(session.read_json("baseline_before.json") or [], after)
    hints = session.hints_taken()
    for rec in records:
        rec["hints"] = hints.get(rec["index"], 0)
        rec["score"] = score(rec, rec["hints"], len(lost))
    total = round(sum(r["score"] for r in records) / len(records)) if records else 0
    started = session.meta.get("started")
    minutes = None
    if started:
        minutes = round((datetime.now() - datetime.fromisoformat(started)).total_seconds() / 60, 1)
    report = {"session": session.path.name, "incident": session.incident, "graded": stamp(),
              "minutes_since_start": minutes, "score": total, "collateral": lost, "faults": records,
              "baseline_after": [r.as_dict() for r in after]}
    if save:
        session.write_json("grade.json", report)
        ev = session.path / "evidence"
        ev.mkdir(exist_ok=True)
        for name, text in evidence_files(records).items():
            (ev / name).write_text(text, encoding="utf-8")
    for rec in records:
        print(f"  Fault {rec['index']}: {rec['grade']:<11} score {rec['score']:>3}  hints {rec['hints']}  "
              + " > ".join(f"{s['question']} {'yes' if s['ok'] else 'NO'}" for s in rec["steps"]), flush=True)
    if lost:
        print("  Collateral (passed at start, failing now): " + "; ".join(lost))
    print(f"  Session score {total}" + (f", {minutes} min since start" if minutes is not None else ""))
    return report


def cmd_grade(args) -> int:
    from .session import Session

    log = cml_api.default_log
    session = Session.find(args.session)
    cml_api.LOG_FILE = session.path / "session.log"
    profile = session.profile()
    model = session.broken_model()
    client = cml_api.connect()
    labs = cml_api.find_incident_labs(client, session.incident)
    if len(labs) != 1:
        raise cml_api.CmlError(f"found {len(labs)} running labs for incident {session.incident}; "
                               f"start it with: python -m breaker start {session.incident}")
    lab = labs[0]
    consoles = _consoles(lab, model, profile)
    try:
        _wait_ready(consoles, model, profile, log, session.incident)
        grade_session(session, lab, consoles, log)
    finally:
        consoles.close()
    print(f"Saved {session.path.relative_to(TOOL_ROOT)}/grade.json and evidence/")
    return 0


def _check_clues(session, log) -> None:
    """Selftest: every key clue must quote a line the broken lab really printed."""
    from .report import evidence_blocks, find_clue

    for inst in session.answers():
        path = session.path / "evidence" / f"broken-state-fault{inst['index']}.txt"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
        hit = find_clue(inst["debrief"].get("key_clue", ""), evidence_blocks(text))
        log(f"key clue for {inst['id']}: " + (f"found in {hit[0]!r}: {hit[1].strip()}" if hit else
                                               "NOT FOUND in the broken-state output; fix key_clue"))


def cmd_debrief(args) -> int:
    """Reveal the answers, sanitize, write debrief.md and update the session index."""
    from .report import LabDoc, index_row, publish_issues, render_debrief, sanitize_session, update_index
    from .session import SESSIONS_DIR, Session

    session = Session.find(args.session)
    grade = session.read_json("grade.json")
    if not grade:
        raise LabModelError(f"no grade.json in {session.path.name} yet; run: python -m breaker grade {session.incident}")
    profile = session.profile()
    answers = session.answers()
    session.reveal()
    for line in sanitize_session(session.path):
        print(f"  sanitized {line}")
    (session.path / "debrief.md").write_text(render_debrief(session.path, session.meta, profile, answers, grade),
                                             encoding="utf-8")
    session.update(debriefed=datetime.now().isoformat(timespec="seconds"))
    publish = [session.path / n for n in ("ticket.md", "debrief.md", "broken.yaml", "grade.json",
                                          "baseline_before.json", "answers.json", "hints.log")]
    publish += sorted((session.path / "evidence").glob("*")) if (session.path / "evidence").exists() else []
    issues = publish_issues(session.path, publish)
    if issues:
        print("Do not publish yet: banner or EULA text is still present in:")
        for issue in issues:
            print(f"  {issue}")
        return 1
    where = session.path.relative_to(TOOL_ROOT)
    print(f"Debrief: {where}/debrief.md (score {grade.get('score', 0)}, "
          f"{', '.join(r['grade'] for r in grade.get('faults') or [])})")
    if session.path.parent.resolve() == SESSIONS_DIR.resolve():
        row = index_row(session.path, session.meta, LabDoc(profile).name, grade)
        if update_index(TOOL_ROOT / "README.md", row, session.path.name):
            print("Session index: troubleshooting/README.md updated")
        print("\nPublish from PowerShell, in the repo folder:\n")
        print(f"  git add troubleshooting/{where.as_posix()} troubleshooting/README.md")
        print("  git status")
        print(f'  git commit -m "Publish break-fix session {session.incident}"')
        print("  git push")
    print(f"\nIf the incident lab is still on the server: python -m breaker clean --id {session.incident} --remove")
    return 0


def _push(consoles, changes: dict[str, list[str]], save: bool, log, what: str) -> None:
    for node, lines in changes.items():
        consoles.configure(node, lines)
        log(f"{what} on {node}: {' / '.join(x.strip() for x in lines)}")
        if save:
            out = consoles.run(node, "write memory", confirm=True)
            if "[OK]" not in out:
                raise cml_api.CmlError(f"{node}: write memory did not report [OK]")


def cmd_selftest(args) -> int:
    """Acceptance for faults on one lab, in ONE import: inject every fault, boot,
    symptoms present, apply every reference_fix -> each Exact, re-inject, apply
    every workaround -> each Temporary. Faults never share a section or a host
    (engine rule 1), so they can be tested side by side."""
    from .faults import by_id, library, pick
    from .session import Session

    log = cml_api.default_log
    profile = LabProfile.load(args.lab)
    model = profile.model()
    if args.all:
        faults = [f for f in library() if f.candidates(model, profile)
                  and (args.include_proven or not f.proven_on(profile.lab))]
    else:
        faults = [by_id(fid) for fid in args.fault]
    if not faults:
        raise LabModelError(f"nothing to test on {profile.lab} (all fitting faults already proven; "
                            "add --include-proven to retest)")
    for fault in faults:
        problems = fault.problems()
        if problems:
            raise LabModelError(f"{fault.id} fails acceptance before any lab: {'; '.join(problems)}")
    healthy = _healthy_baseline(profile.lab)
    if not healthy:
        raise LabModelError(f"no passing healthy baseline for {profile.lab} in runs/ (engine rule 7)")
    tier = max(max(f.tier for f in faults), 1)
    broken, instances = pick(model, profile, tier, args.seed, only=[f.id for f in faults])
    base = RUNS_DIR / "selftest"
    base.mkdir(parents=True, exist_ok=True)
    incident = f"{profile.lab[:2]}-st-{args.seed}"
    session = Session.create(profile, broken, instances, tier, args.seed,
                             _ticket(profile, model, instances, tier, args.seed, healthy),
                             base=base, incident=incident)
    for inst in instances:
        log(f"selftest {inst['id']} on {profile.lab}: {', '.join(f'{n} {h}' for n, h in inst['touched'])}")
    stages: list[dict] = []          # one entry per stage, with every fault's grade
    state: dict = {"keep_open": True}
    consoles = None
    t0 = time.monotonic()
    want = {"broken": {"Open", "Partial", "Temporary"}, "reference-fix": {"Exact"}, "workaround": {"Temporary"}}
    try:
        # start_session saves each fault's broken-state show output in evidence/,
        # where the key clue must be found (report.find_clue).
        lab, consoles = start_session(args, session=session, state=state)
        _check_clues(session, log)

        def stage(name: str) -> None:
            log(f"selftest stage: {name} ({len(instances)} fault(s))")
            report = grade_session(session, lab, consoles, log, save=False, collateral_check=False)
            grades = {rec["id"]: rec for rec in report["faults"]}
            stages.append({"stage": name, "grades": grades})
            session.write_json(f"selftest-{len(stages)}-{name}.json", report)
            for fid, rec in grades.items():
                ok = rec["grade"] in want[name]
                log(f"{name}: {fid} graded {rec['grade']} ({'as expected' if ok else 'expected ' + ' or '.join(sorted(want[name]))})")

        stage("broken")
        for inst in instances:
            _push(consoles, inst["reference_fix"], True, log, f"reference fix ({inst['id']})")
        stage("reference-fix")
        for inst in instances:
            _push(consoles, inst["selftest"]["reinject"], True, log, f"re-inject ({inst['id']})")
        for inst in instances:
            _push(consoles, inst["selftest"]["workaround"], inst["selftest"]["workaround_saved"], log,
                  f"workaround ({inst['id']})")
        stage("workaround")
    finally:
        if consoles is not None:
            consoles.close()
        minutes = round((time.monotonic() - t0) / 60, 1)
        print(f"\nselftest on {profile.lab}, {len(instances)} fault(s) in one import, {minutes} min:")
        all_passed = True
        for inst, fault in zip(instances, faults):
            mine = [{"stage": st["stage"], "grade": st["grades"][fault.id]["grade"], "want": sorted(want[st["stage"]]),
                     "ok": st["grades"][fault.id]["grade"] in want[st["stage"]],
                     "steps": st["grades"][fault.id]["steps"], "record": st["grades"][fault.id]}
                    for st in stages if fault.id in st["grades"]]
            passed = len(mine) == 3 and all(m["ok"] for m in mine)
            all_passed &= passed
            result = {"fault": fault.id, "lab": profile.lab, "seed": args.seed, "passed": passed,
                      "minutes": minutes, "batch": [i["id"] for i in instances], "target": inst["touched"],
                      "stages": mine, "session": str(session.path.relative_to(TOOL_ROOT))}
            (RUNS_DIR / f"selftest-{fault.id}-{profile.lab[:2]}.json").write_text(json.dumps(result, indent=2),
                                                                                    encoding="utf-8")
            line = "  ".join(f"{m['stage']} {m['grade']}{'' if m['ok'] else ' (!)'}" for m in mine)
            print(f"  {'PASS' if passed else 'FAIL'}  {fault.id:<22} {line}")
            if passed and not fault.proven_on(profile.lab):
                print(f"        add {profile.lab[:2]!r} to selftest_passed in {fault.path.relative_to(TOOL_ROOT)}")
        lab = state.get("lab")
        if lab is not None and not args.keep:
            try:
                cml_api.stop_and_wipe(lab, remove=True, log=log)
            except Exception as exc:
                log(f"cleanup failed ({exc}); run: python -m breaker clean --id {incident} --remove")
    return 0 if all_passed else 1


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


def cmd_gui(args) -> int:
    """Local web console over these same commands (build step 6)."""
    try:
        from .gui.app import serve
    except ImportError as exc:
        raise LabModelError(f"the GUI needs Flask and Markdown ({exc}); run: pip install -r requirements.txt")
    serve(port=args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="breaker", description="CML break-fix troubleshooting tool")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="the six labs, node counts, profiles and fault count")

    s = sub.add_parser("show", help="parsed view of one lab: nodes, features, cabling")
    s.add_argument("lab", help="lab folder or prefix, e.g. 03")

    sub.add_parser("doctor", help="check credentials, TLS, login and running-node headroom")

    s = sub.add_parser("break", help="pick faults for a lab and write a session (ticket, broken.yaml)")
    s.add_argument("lab", help="lab folder or prefix, e.g. 03")
    s.add_argument("--tier", type=int, required=True, choices=[1, 2, 3], help="difficulty tier")
    s.add_argument("--seed", type=int, help="same seed = same faults and ticket (default: random)")
    s.add_argument("--fault", action="append", help="practise a specific fault id (repeatable)")

    s = sub.add_parser("start", help="start a session (import the broken lab), or a healthy lab with --healthy")
    s.add_argument("lab", help="session incident id (e.g. 03-t2-s4127), or a lab with --healthy, e.g. 03")
    s.add_argument("--healthy", action="store_true", help="import the unbroken topology.yaml")
    s.add_argument("--id", help="incident id for the title suffix (default: <lab#>-healthy-HHMM)")
    s.add_argument("--clean-after", action="store_true",
                   help="stop, wipe and delete the lab when done (pass or fail); results stay in runs/")

    s = sub.add_parser("hint", help="unlock the next hint for one fault (in order, logged)")
    s.add_argument("session", help="session incident id, e.g. 03-t2-s4127")
    s.add_argument("fault", type=int, help="fault number from the ticket")

    s = sub.add_parser("grade", help="grade the running session lab live and save evidence")
    s.add_argument("session", help="session incident id, e.g. 03-t2-s4127")

    s = sub.add_parser("debrief", help="reveal the answers, sanitize, write debrief.md, update the session index")
    s.add_argument("session", help="session incident id, e.g. 03-t2-s4127")

    s = sub.add_parser("selftest", help="acceptance test for faults on one lab, all in one import (cleans up)")
    s.add_argument("fault", nargs="*", help="fault ids, e.g. ospf-area-mismatch l1-shutdown")
    s.add_argument("--lab", required=True, help="lab folder or prefix, e.g. 03")
    s.add_argument("--all", action="store_true", help="every fault that fits the lab and is not proven there yet")
    s.add_argument("--include-proven", action="store_true", help="with --all, retest proven faults too")
    s.add_argument("--seed", type=int, default=1, help="target choice seed (default 1)")
    s.add_argument("--keep", action="store_true", help="leave the lab running afterwards")

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

    s = sub.add_parser("gui", help="local web console: open http://localhost:5050 in the Windows browser")
    s.add_argument("--port", type=int, default=5050, help="port on 127.0.0.1 (default 5050)")

    s = sub.add_parser("clean", help="stop and wipe incident labs only")
    s.add_argument("--id", help="only the lab with this incident id")
    s.add_argument("--remove", action="store_true", help="also delete the lab from the server")
    s.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    return p


COMMANDS = {"list": cmd_list, "show": cmd_show, "doctor": cmd_doctor, "break": cmd_break, "start": cmd_start,
            "hint": cmd_hint, "grade": cmd_grade, "debrief": cmd_debrief, "selftest": cmd_selftest, "rebuild": cmd_rebuild,
            "baseline": cmd_baseline, "console": cmd_console, "clean": cmd_clean, "gui": cmd_gui}


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
