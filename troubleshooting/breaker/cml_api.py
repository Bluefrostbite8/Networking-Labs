"""Everything that talks to CML: connect, capacity check, import, boot,
console access through pyATS, VLAN/VTP rebuild, stop and wipe.

Safety rules enforced here, not left to callers:
  * credentials come only from VIRL2_URL / VIRL2_USER / VIRL2_PASS / CML_CA_BUNDLE,
    and TLS is always verified against the exported CML certificate;
  * stop, wipe and remove refuse any lab whose title lacks the
    " [incident <id>]" suffix, re-reading the title from the server first;
  * pyATS connects without its default init config, which would otherwise
    rewrite ``line console 0`` and ``line vty 0 4`` on every node.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from .lab_model import INFRA_DEFINITIONS, TOOL_ROOT, LabModel, LabProfile

ENV_VARS = ("VIRL2_URL", "VIRL2_USER", "VIRL2_PASS", "CML_CA_BUNDLE")
INCIDENT_RE = re.compile(r" \[incident ([A-Za-z0-9][A-Za-z0-9_.-]*)\]$")
CONFIG_PATH = TOOL_ROOT / "config.yaml"
MAX_TITLE = 64  # CML lab title limit; keeps "<title> [incident <id>]" importable

# unicon's defaults run "no logging console", "line console 0 / exec-timeout 0"
# and "line vty 0 4 / exec-timeout 0" on connect. An empty list runs nothing.
SAFE_INIT_EXEC = ["terminal length 0", "terminal width 0"]
SAFE_INIT_CONFIG: list[str] = []

CLI_ERROR_RE = re.compile(r"^\s*%\s*(Invalid|Incomplete|Ambiguous|Unrecognized|Error)", re.I | re.M)

Log = Callable[[str], None]


class CmlError(RuntimeError):
    """A CML step failed; the message says what to do next."""


class UnsafeOperation(CmlError):
    """Refused: the target lab is not an incident lab."""


LOG_FILE: Path | None = None  # set by the CLI per run; every log line is also appended here


def default_log(msg: str) -> None:
    """Print and file a log line. Never raises: a closed terminal or pipe (Ctrl+C
    also kills a `| tee`) must not stop a cleanup that is still running."""
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        print(line, flush=True)
    except (OSError, ValueError):
        pass
    if LOG_FILE is not None:
        try:
            with LOG_FILE.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


# --------------------------------------------------------------------------- config and connection


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        raise CmlError(
            f"{CONFIG_PATH.name} not found. Copy config.example.yaml to config.yaml "
            "in troubleshooting/ and set node_cap."
        )
    cfg = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(cfg.get("node_cap"), int) or cfg["node_cap"] < 1:
        raise CmlError("config.yaml: node_cap must be a whole number (your CML licence node limit)")
    return cfg


def credentials() -> dict[str, str]:
    missing = [v for v in ENV_VARS if not os.environ.get(v)]
    if missing:
        raise CmlError(f"missing environment variables: {', '.join(missing)} (set them in ~/.bashrc)")
    env = {v: os.environ[v] for v in ENV_VARS}
    if not env["VIRL2_URL"].startswith("https://"):
        raise CmlError("VIRL2_URL must start with https:// (TLS is never disabled)")
    ca = Path(env["CML_CA_BUNDLE"]).expanduser()
    if not ca.is_file():
        raise CmlError(f"CML_CA_BUNDLE points to {ca}, which is not a file")
    env["CML_CA_BUNDLE"] = str(ca)
    return env


def connect():
    """Logged-in ClientLibrary with TLS verified against CML_CA_BUNDLE."""
    from virl2_client import ClientLibrary

    env = credentials()
    try:
        return ClientLibrary(
            env["VIRL2_URL"],
            env["VIRL2_USER"],
            env["VIRL2_PASS"],
            ssl_verify=env["CML_CA_BUNDLE"],
            raise_for_auth_failure=True,
        )
    except Exception as exc:  # surface the real cause in one line
        raise CmlError(f"could not log in to {env['VIRL2_URL']}: {type(exc).__name__}: {exc}") from exc


# --------------------------------------------------------------------------- titles and the safety guard


def incident_title(base_title: str, incident_id: str) -> str:
    title = f"{base_title} [incident {incident_id}]"
    if not INCIDENT_RE.search(title):
        raise CmlError(f"invalid incident id {incident_id!r} (letters, digits, _ . - only)")
    if len(title) > MAX_TITLE:
        raise CmlError(f"lab title {title!r} is {len(title)} characters; CML allows {MAX_TITLE}")
    return title


def incident_id_of(title: str | None) -> str | None:
    m = INCIDENT_RE.search(title or "")
    return m.group(1) if m else None


def server_title(lab) -> str:
    """The lab's title as the server has it now (not a cached local copy)."""
    try:
        details = lab.details()
        return details.get("lab_title") or details.get("title") or ""  # missing title = refused
    except Exception as exc:
        raise CmlError(f"could not read the title of lab {lab.id}: {exc}") from exc


def _guard(lab) -> str:
    title = server_title(lab)
    if incident_id_of(title) is None:
        raise UnsafeOperation(f"refusing to touch lab {lab.id} {title!r}: no ' [incident <id>]' suffix")
    return title


def find_incident_labs(client, incident_id: str | None = None) -> list:
    out = []
    for lab in client.all_labs():
        found = incident_id_of(server_title(lab))
        if found and (incident_id is None or found == incident_id):
            out.append(lab)
    return out


def stop_and_wipe(lab, remove: bool = False, log: Log = default_log) -> None:
    title = _guard(lab)
    log(f"stopping {title}")
    lab.stop(wait=True)
    _guard(lab)
    log(f"wiping {title}")
    lab.wipe(wait=True)
    if remove:
        _guard(lab)
        log(f"removing {title}")
        lab.remove()


# --------------------------------------------------------------------------- capacity


@dataclass
class LabUsage:
    title: str
    id: str
    running: int


def node_usage(client) -> list[LabUsage]:
    """Running nodes per lab that count against the licence."""
    usage = []
    for lab in client.all_labs():
        lab.sync_states()
        n = sum(1 for node in lab.nodes() if node.is_active() and node.node_definition not in INFRA_DEFINITIONS)
        if n:
            usage.append(LabUsage(lab.title, lab.id, n))
    return usage


def check_capacity(client, needed: int, cap: int) -> list[LabUsage]:
    usage = node_usage(client)
    running = sum(u.running for u in usage)
    if running + needed <= cap:
        return usage
    lines = [f"  {u.running:>3}  {u.title}  (id {u.id})" for u in sorted(usage, key=lambda u: -u.running)]
    raise CmlError(
        f"not enough licence headroom: {running} nodes running + {needed} needed > cap {cap}.\n"
        f"Stop {running + needed - cap} or more running nodes first. Running now:\n" + "\n".join(lines) +
        "\nNothing was imported or started."
    )


# --------------------------------------------------------------------------- import and boot


def import_lab(client, model: LabModel, incident_id: str, log: Log = default_log):
    title = incident_title(model.title, incident_id)
    if client.find_labs_by_title(title):
        raise CmlError(f"a lab titled {title!r} already exists; clean it or pick another id")
    lab = client.import_lab(model.to_yaml(title=title), title=title)
    log(f"imported {title} as lab {lab.id}")
    return lab


def boot(lab, timeout_min: float, log: Log = default_log, poll_s: int = 15) -> None:
    lab.start(wait=False)
    wait_booted(lab, None, timeout_min, log, poll_s)


def wait_booted(lab, labels: set[str] | None, timeout_min: float, log: Log = default_log, poll_s: int = 15) -> None:
    deadline = time.monotonic() + timeout_min * 60
    last = None
    while True:
        lab.sync_states()
        nodes = [n for n in lab.nodes() if labels is None or n.label in labels]
        pending = sorted(n.label for n in nodes if not n.is_booted())
        if not pending:
            log(f"all {len(nodes)} nodes booted")
            return
        if pending != last:
            log(f"waiting for {len(pending)} node(s) to boot: {', '.join(pending)}")
            last = pending
        if time.monotonic() > deadline:
            raise CmlError(f"boot timeout after {timeout_min} min; still booting: {', '.join(pending)}")
        time.sleep(poll_s)


# --------------------------------------------------------------------------- consoles


class Consoles:
    """Console sessions to lab nodes through the CML terminal server (pyATS)."""

    def __init__(self, lab, hosts: set[str] | frozenset = frozenset()) -> None:
        self.hosts = set(hosts)  # Linux nodes: no IOS init commands on connect
        # Genie's file-transfer helper looks for "terminal_server" among testbed
        # file servers; CML defines it as a device, so it warns on every connect.
        # The tool never transfers files, so those warnings are noise.
        logging.getLogger("genie.libs.filetransferutils").setLevel(logging.ERROR)
        env = credentials()
        self.lab = lab
        lab.pyats.sync_testbed(env["VIRL2_USER"], env["VIRL2_PASS"])
        self.pyats = lab.pyats
        self._ready: set[str] = set()
        self._tune_console_wakeup()

    def _tune_console_wakeup(self) -> None:
        """Keep pressing Enter longer, and give up sooner, when a console is quiet.

        Right after boot an IOSvL2 console sits at "Press RETURN to get started!"
        while it generates RSA keys and ignores input. unicon's default presses
        Enter for about 28 s, then waits up to 8 min (iosv CONNECTION_TIMEOUT 480)
        for a prompt that never comes. 15 tries with its growing wait cover about
        2 min; a failed attempt then ends in 2 min and wait_consoles retries.
        Uses ClPyats._testbed (virl2-client is pinned to 2.10.1); CML names each
        node's console connection "a".
        """
        overrides = {"ESCAPE_CHAR_PROMPT_WAIT_RETRIES": 15, "CONNECTION_TIMEOUT": 120}
        testbed = getattr(self.pyats, "_testbed", None)
        if testbed is None:
            return
        for name, device in testbed.devices.items():
            if name != "terminal_server" and "a" in device.connections:
                device.connections["a"].setdefault("settings", {}).update(overrides)

    def run(self, node: str, command: str, confirm: bool = False) -> str:
        """Exec-mode command. ``confirm`` answers IOS [confirm] prompts."""
        kwargs = {}
        if confirm:
            from unicon.eal.dialogs import Dialog, Statement

            self._ensure(node)  # extra kwargs must not reach a fresh connect()
            kwargs["reply"] = Dialog(
                [Statement(pattern=r"\[confirm\]", action="sendline()", loop_continue=True, continue_timer=False)]
            )
        init_exec = [] if node in self.hosts else SAFE_INIT_EXEC
        try:
            out = self.pyats.run_command(
                node, command, init_exec_commands=init_exec, init_config_commands=SAFE_INIT_CONFIG, **kwargs
            )
        except Exception as exc:
            _raise_from_unicon(node, command, exc)
        self._ready.add(node)
        _raise_on_cli_error(node, command, out)
        return out

    def configure(self, node: str, lines: list[str]) -> str:
        try:
            out = self.pyats.run_config_command(
                node, "\n".join(lines), init_exec_commands=SAFE_INIT_EXEC, init_config_commands=SAFE_INIT_CONFIG
            )
        except Exception as exc:
            _raise_from_unicon(node, "; ".join(line.strip() for line in lines), exc)
        self._ready.add(node)
        _raise_on_cli_error(node, "; ".join(line.strip() for line in lines), out)
        return out

    def console_log(self, node: str, lines: int = 200) -> str:
        """CML's own buffer of what the node's console printed (no pyATS involved)."""
        raw = self.lab.get_node_by_label(node).console_logs(0, lines=lines)
        if isinstance(raw, dict):
            return str(raw.get("log") or "\n".join(str(v) for v in raw.values()))
        if isinstance(raw, list):
            return "\n".join(str(x) for x in raw)
        return str(raw)

    def _ensure(self, node: str) -> None:
        if node not in self._ready:
            self.run(node, "show clock")

    def close(self) -> None:
        try:
            self.pyats.cleanup()
        except Exception:
            pass


def _raise_from_unicon(node: str, command: str, exc: Exception) -> None:
    """unicon raises SubCommandFailure itself when IOS answers '% Invalid input';
    turn that into one readable CmlError. Connection problems pass through."""
    if type(exc).__name__ != "SubCommandFailure":
        raise exc
    texts: list[str] = []

    def collect(obj, depth=0):
        if depth > 4:
            return
        if isinstance(obj, str):
            texts.append(obj)
        elif isinstance(obj, BaseException):
            for arg in obj.args:
                collect(arg, depth + 1)
            collect(obj.__cause__, depth + 1)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                collect(item, depth + 1)

    collect(exc)
    text = "\n".join(texts).replace("\r", "")
    m = CLI_ERROR_RE.search(text)
    detail = text[m.start():].splitlines()[0].strip() if m else "command failed"
    raise CmlError(f"{node} rejected '{command}': {detail}") from exc


def _raise_on_cli_error(node: str, command: str, output: str) -> None:
    m = CLI_ERROR_RE.search(output or "")
    if m:
        line = output[m.start():].splitlines()[0].strip()
        raise CmlError(f"{node} rejected '{command}': {line}")


def wait_consoles(consoles: Consoles, labels: list[str], ready_check: str, timeout_min: float,
                  log: Log = default_log, poll_s: int = 15,
                  on_fail: Callable[[str, str], None] | None = None) -> None:
    """``on_fail(label, console_text)`` receives CML's console buffer after a failed attempt."""
    deadline = time.monotonic() + timeout_min * 60
    pending = list(labels)
    while pending:
        for label in list(pending):
            log(f"connecting to {label} console")
            try:
                if consoles.run(label, ready_check).strip():
                    pending.remove(label)
                    log(f"console ready: {label}")
            except Exception as exc:
                reason = " ".join(str(exc).split())[:160]
                log(f"{label} console not ready ({type(exc).__name__}: {reason}); will retry")
                if on_fail:
                    try:
                        on_fail(label, consoles.console_log(label))
                    except Exception as log_exc:  # diagnostics must never stop the run
                        log(f"could not read {label}'s console log: {log_exc}")
                if time.monotonic() > deadline:
                    raise CmlError(f"console on {label} never answered '{ready_check}': {exc}") from exc
        if pending:
            if time.monotonic() > deadline:
                raise CmlError(f"consoles not ready: {', '.join(pending)}")
            time.sleep(poll_s)


def console_power_cycle(lab, consoles: Consoles, spec: dict, timeout_min: float,
                        log: Log = default_log, poll_s: int = 30) -> str:
    """Power-cycle one node so its console comes up where pyATS can reach it.

    A Catalyst 9800-CL (like other virtual IOS-XE) picks its console in GRUB,
    before the config loads: on a fresh import IOS-XE runs on the VGA console
    even with 'platform console serial' in the config, which only takes effect
    from the next boot. ``ready_ping`` waits until the node answers on its
    management address (IOS-XE up, config applied) before the cycle.
    Returns the node label.
    """
    from .checks import _ping_received

    label = spec["node"]
    ping = spec.get("ready_ping")
    if ping:
        deadline = time.monotonic() + timeout_min * 60
        log(f"waiting for {label} to answer {ping['to']} from {ping['from']} (first boot, VGA console)")
        while _ping_received(consoles.run(ping["from"], f"ping {ping['to']}")) < 3:
            if time.monotonic() > deadline:
                raise CmlError(f"{label} never answered {ping['to']} from {ping['from']} on its first boot")
            time.sleep(poll_s)
        log(f"{label} is up on its first boot; settling {spec.get('settle_s', 30)} s before the power cycle")
        time.sleep(spec.get("settle_s", 30))
    node = lab.get_node_by_label(label)
    node.stop(wait=True)
    node.start(wait=False)
    log(f"power-cycled {label} so IOS-XE starts on the serial console")
    wait_booted(lab, {label}, timeout_min, log)
    return label


# --------------------------------------------------------------------------- VLAN / VTP rebuild


def vlan_lines(vlans: dict[int, str | None]) -> list[str]:
    lines = []
    for vid, name in sorted(vlans.items()):
        lines.append(f"vlan {vid}")
        if name:
            lines.append(f" name {name}")
        lines.append(" exit")
    return lines


def parse_vlans(output: str) -> dict[int, tuple[str, str]]:
    """{vlan: (name, state)} from 'show vlan'. Genie first, regex fallback."""
    try:
        from genie.conf.base import Device

        dev = Device("parser", os="ios")
        dev.custom = {"abstraction": {"order": ["os"]}}
        parsed = dev.parse("show vlan", output=output)
        return {int(k): (v.get("name", ""), v.get("state", "")) for k, v in parsed.get("vlans", {}).items()}
    except Exception:
        found = {}
        for m in re.finditer(r"^(\d{1,4})\s+(\S+)\s+(active|suspended|act/\S+)", output, re.M):
            found[int(m.group(1))] = (m.group(2), m.group(3))
        return found


def count_dhcp_bindings(output: str) -> int:
    """Rows in 'show ip dhcp binding' (no Genie parser for IOS)."""
    return len(re.findall(r"^\d{1,3}(?:\.\d{1,3}){3}\s+\S+", output, re.M))


def rebuild(lab, consoles: Consoles, model: LabModel, profile: LabProfile, log: Log = default_log) -> None:
    """Restore what the export can't carry, from the lab profile (before the clock)."""
    rb = profile.rebuild
    vlans = profile.vlans
    switches = [n.label for n in model.switches() if n.label not in profile.never_touch]
    vtp = rb.get("vtp")

    if vtp:
        primary = vtp["primary_server"]
        log(f"VTP v{vtp['version']} domain {vtp['domain']} on {len(switches)} switches, primary {primary}")
        for sw in switches:
            consoles.configure(sw, [f"vtp domain {vtp['domain']}", f"vtp version {vtp['version']}"])
        for sw in switches:
            if sw != primary:
                consoles.configure(sw, ["vtp mode client"])
        consoles.configure(primary, ["vtp mode server"])
        out = consoles.run(primary, f"vtp primary {vtp.get('feature', 'vlan')}", confirm=True)
        log(f"{primary}: {' '.join(out.split())[:120]}")
        if vlans:
            consoles.configure(primary, vlan_lines(vlans))
    elif vlans:
        for sw in switches:
            consoles.configure(sw, vlan_lines(vlans))

    if vlans:
        wait_vlans(consoles, switches, vlans, rb.get("vlan_timeout_s", 300), log)
        # New VLANs start new spanning-tree instances; until their ports leave
        # listening/learning, SVIs stay down, HSRP sits in Init and DHCP and
        # pings fail. Wait for that before leases are renewed or checks run.
        wait_stp_settled(consoles, switches, rb.get("stp_settle_s", 120), log)
        recover_stuck_svis(consoles, switches, vlans, log)

    if rb.get("clear_sticky"):
        # IOSvL2 15.2 has no "clear port-security" exec command; deleting the
        # saved address on the interface is the supported way to remove it.
        for sw in switches:
            for iface, macs in model.node(sw).config.sticky_macs().items():
                running = consoles.run(sw, f"show running-config interface {iface}")
                present = [mac for mac in macs if f"mac-address sticky {mac}" in running]
                if not present:  # already removed (e.g. a second rebuild run)
                    continue
                lines = [f"interface {iface}"]
                lines += [f" no switchport port-security mac-address sticky {mac}" for mac in present]
                consoles.configure(sw, lines)
                log(f"{sw}: removed saved sticky MAC {', '.join(present)} on {iface}")

    leases = rb.get("renew_leases")
    if leases:
        renew_leases(lab, consoles, leases, log)

    if rb.get("save", True):
        for sw in switches:
            out = consoles.run(sw, "write memory", confirm=True)
            if "[OK]" not in out:
                raise CmlError(f"{sw}: write memory did not report [OK]")
        log(f"saved running config on {len(switches)} switches")


def wait_vlans(consoles: Consoles, switches: list[str], vlans: dict[int, str | None], timeout_s: int,
               log: Log = default_log, poll_s: int = 10) -> None:
    deadline = time.monotonic() + timeout_s
    pending = list(switches)
    problems: dict[str, str] = {}
    while pending:
        for sw in list(pending):
            have = parse_vlans(consoles.run(sw, "show vlan"))
            bad = [
                str(v) for v, name in vlans.items()
                if v not in have or have[v][1] != "active" or (name and have[v][0] != name)
            ]
            if bad:
                problems[sw] = ", ".join(bad)
            else:
                pending.remove(sw)
                problems.pop(sw, None)
        if pending:
            if time.monotonic() > deadline:
                detail = "; ".join(f"{sw} missing {problems[sw]}" for sw in pending)
                raise CmlError(f"VLANs did not reach every switch: {detail}")
            time.sleep(poll_s)
    log(f"VLANs {', '.join(map(str, sorted(vlans)))} active on all {len(switches)} switches")


def stp_transitioning(output: str) -> int | None:
    """Ports in listening or learning, from the totals of 'show spanning-tree summary'."""
    from .checks import genie_parse
    parsed = genie_parse("show spanning-tree summary", output)
    totals = (parsed or {}).get("total_statistics")
    if totals:
        return int(totals.get("listenings", 0)) + int(totals.get("learnings", 0))
    m = re.search(r"^\d+\s+(?:vlans?|msts?)\s+\d+\s+(\d+)\s+(\d+)\s+\d+\s+\d+", output, re.M)
    return int(m.group(1)) + int(m.group(2)) if m else None


def wait_stp_settled(consoles: Consoles, switches: list[str], timeout_s: int,
                     log: Log = default_log, poll_s: int = 5) -> None:
    """Wait until no switch has a port in listening or learning. Never fails the
    run: after ``timeout_s`` it logs what is still moving and carries on."""
    t0 = time.monotonic()
    pending = list(switches)
    moving: dict[str, int | None] = {}
    while pending:
        for sw in list(pending):
            n = stp_transitioning(consoles.run(sw, "show spanning-tree summary"))
            if n == 0:
                pending.remove(sw)
                moving.pop(sw, None)
            else:
                moving[sw] = n
        if not pending:
            break
        if time.monotonic() - t0 > timeout_s:
            still = ", ".join(f"{sw} ({moving[sw] if moving[sw] is not None else 'unparsed'})" for sw in pending)
            log(f"spanning tree still moving after {timeout_s} s on {still}; continuing")
            return
        time.sleep(poll_s)
    log(f"spanning tree settled on all {len(switches)} switches after {time.monotonic() - t0:.0f} s")


def down_svis(output: str) -> list[str]:
    """SVIs with an address that are down/down (not administratively down),
    from 'show ip interface brief'."""
    from .checks import genie_parse
    parsed = genie_parse("show ip interface brief", output)
    if parsed:
        rows = [(name, i.get("ip_address", ""), i.get("status", ""))
                for name, i in (parsed.get("interface") or {}).items()]
    else:
        rows = [(m.group(1), m.group(2), m.group(3).strip()) for m in re.finditer(
            r"^(Vlan\d+)\s+(\S+)\s+\S+\s+\S+\s+(administratively down|up|down)\s", output, re.M)]
    return [name for name, ip, status in rows
            if name.startswith("Vlan") and ip != "unassigned" and status == "down"]


def recover_stuck_svis(consoles: Consoles, switches: list[str], vlans: dict[int, str | None],
                       log: Log = default_log, wait_s: int = 30, poll_s: int = 5) -> None:
    """IOSvL2 quirk: an SVI whose VLAN was auto-created at boot (an access port
    named it before the rebuild) can stay down/down after the VLAN is active and
    forwarding. Runs after spanning tree settles, so a down SVI here is stuck, not
    converging. Bounces only those SVIs, in rebuilt VLANs; never admin-down ones."""
    wanted = {f"Vlan{v}" for v in vlans}
    for sw in switches:
        stuck = [s for s in down_svis(consoles.run(sw, "show ip interface brief")) if s in wanted]
        if not stuck:
            continue
        lines = []
        for svi in stuck:
            lines += [f"interface {svi}", " shutdown", " no shutdown"]
        consoles.configure(sw, lines)
        deadline = time.monotonic() + wait_s
        still = stuck
        while still and time.monotonic() < deadline:
            time.sleep(poll_s)
            still = [s for s in down_svis(consoles.run(sw, "show ip interface brief")) if s in stuck]
        log(f"{sw}: {', '.join(stuck)} stuck down after the VLAN rebuild; bounced"
            + (f", still down: {', '.join(still)}" if still else ", now up"))


def renew_leases(lab, consoles: Consoles, leases: dict, log: Log = default_log) -> None:
    """Restart the DHCP hosts so they lease now that the VLANs exist, then count bindings."""
    hosts = list(leases.get("hosts") or [])
    raw_server = leases.get("dhcp_server")
    servers = raw_server if isinstance(raw_server, list) else ([raw_server] if raw_server else [])

    def bindings() -> int:  # summed: lab 04 runs two uncoordinated servers on the same scopes
        return sum(count_dhcp_bindings(consoles.run(s, "show ip dhcp binding")) for s in servers)

    where = "+".join(s.strip() for s in servers)
    if servers:
        have = bindings()
        if have >= len(hosts):  # every host already leased at boot: no restart needed
            log(f"{where}: {have} DHCP bindings already, hosts not restarted")
            return
    for label in hosts:
        node = lab.get_node_by_label(label)
        node.stop(wait=True)
        node.start(wait=False)
    log(f"restarted {len(hosts)} hosts: {', '.join(hosts)}")
    wait_booted(lab, set(hosts), leases.get("boot_timeout_min", 5), log)
    if not servers:
        return
    deadline = time.monotonic() + leases.get("timeout_s", 240)
    while True:
        count = bindings()
        if count >= len(hosts):
            log(f"{where}: {count} DHCP bindings")
            return
        if time.monotonic() > deadline:
            raise CmlError(f"{where} shows {count} DHCP bindings, expected {len(hosts)}")
        time.sleep(10)
