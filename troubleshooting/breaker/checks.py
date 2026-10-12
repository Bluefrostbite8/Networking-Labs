"""Live state checks: Genie parsers first, regex fallback, never config text.

Baseline check kinds (step 3), intent checks the grader reuses, and the state
checks fault files use for functional, root-cause and workaround tests (step 4).
Every result keeps the raw output it judged, as evidence.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .lab_model import LabModel, LabProfile, canonical_ifname

INTENT_KINDS = ("stp_root", "hsrp_active", "ospf_neighbors", "eigrp_neighbors", "etherchannel", "route_via")


@dataclass
class Result:
    name: str
    ok: bool
    detail: str
    evidence: list[dict[str, str]] = field(default_factory=list)  # [{node, command, output}]
    optional: bool = False  # reported, but never fails the baseline (e.g. depends on the Internet)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail, "optional": self.optional,
                "evidence": self.evidence}


# --------------------------------------------------------------------------- parsing


def genie_parse(command: str, output: str) -> dict | None:
    """Parse with the IOS Genie parser, then the IOS-XE one (same CLI format on
    these images); None when neither exists or the output doesn't fit."""
    try:
        from genie.conf.base import Device
    except ImportError:
        return None
    for os_name in ("ios", "iosxe"):
        try:
            dev = Device("parser", os=os_name)
            dev.custom = {"abstraction": {"order": ["os"]}}
            parsed = dev.parse(command, output=output)
            if parsed:
                return parsed
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------- the checker


class Checker:
    """Runs checks over live consoles. ``run(node, cmd)`` returns raw CLI output."""

    def __init__(self, run: Callable[[str, str], str], model: LabModel, profile: LabProfile) -> None:
        self.run = run
        self.model = model
        self.profile = profile
        self._cache: dict[tuple[str, str], str] = {}
        self.stale_leases: list[str] = []
        # Sessions only (start and grade): hosts restarted by the grader can leave
        # bindings behind. Healthy runs keep every binding as a target.
        self.skip_stale_leases = False
        self.sleep: Callable[[float], None] = time.sleep  # replaced in offline tests

    def show(self, node: str, command: str) -> str:
        key = (node, command)
        if key not in self._cache:
            self._cache[key] = self.run(node, command)
        return self._cache[key]

    def fresh(self) -> None:
        """Forget cached output (call between check passes so state is re-read)."""
        self._cache.clear()

    # ---- baseline

    def baseline(self) -> list[Result]:
        results: list[Result] = []
        for item in self.profile.raw.get("baseline") or []:
            results += self.run_item(item)
        return results

    def run_item(self, item: dict[str, Any]) -> list[Result]:
        if "intent" in item:
            wanted = INTENT_KINDS if item["intent"] == "all" else _as_list(item["intent"])
            out: list[Result] = []
            intent = self.profile.raw.get("intent") or {}
            for kind in wanted:
                if kind in intent:
                    out += getattr(self, f"intent_{kind}")(intent[kind])
            return out
        (kind, spec), = item.items()
        handler = getattr(self, f"check_{kind}", None)
        if handler is None:
            return [Result(kind, False, f"unknown check kind {kind!r}")]
        nodes = self._nodes(spec.get("on") or spec.get("from"))
        targets = [nodes] if spec.get("total") else nodes  # total: one result over all nodes
        out = []
        for node in targets:
            try:
                out.append(handler(node, spec))
            except Exception as exc:  # a dead console must fail the check, not the run
                out.append(Result(f"{kind} {node}", False, f"{type(exc).__name__}: {exc}"))
        for r in out:
            r.optional = bool(spec.get("optional"))
        return out

    def _nodes(self, value) -> list[str]:
        if value == "switches":
            return [n.label for n in self.model.switches() if n.label not in self.profile.never_touch]
        if value == "ios":  # every IOS-family node the tool may touch
            return [n.label for n in self.model.ios_nodes() if n.label not in self.profile.never_touch]
        return _as_list(value)

    def _ev(self, node: str, command: str) -> dict[str, str]:
        return {"node": node, "command": command, "output": self.show(node, command)}

    # ---- intent checks (also used by the grader)

    def intent_stp_root(self, spec: dict) -> list[Result]:
        out = []
        for vlan, node in spec.items():
            cmd = f"show spanning-tree vlan {vlan}"
            raw = self.show(node, cmd)
            parsed = genie_parse(cmd, raw)
            vlans = next(iter(parsed.values()), {}).get("vlans", {}) if parsed else {}
            v = vlans.get(int(vlan)) or vlans.get(str(vlan)) or {}  # Genie keys VLANs as int
            if v:
                is_root = v.get("root", {}).get("address") == v.get("bridge", {}).get("address")
            else:  # no parser, or an MST-mode layout Genie keys differently
                is_root = "This bridge is the root" in raw
            out.append(Result(f"stp_root vlan {vlan}", is_root,
                              f"{node} is root" if is_root else f"{node} is not the root", [self._ev(node, cmd)]))
        return out

    # HSRP states a group passes through on its way to Active or Standby. A group
    # still in one of these right after VLANs are created (SVI waiting on STP) or
    # right after a fix is re-read for up to HSRP_SETTLE_S before it is judged.
    HSRP_TRANSIENT = {"init", "listen", "speak"}
    HSRP_SETTLE_S = 90
    HSRP_POLL_S = 10

    def _hsrp_state(self, node: str, vlan: str) -> str | None:
        cmd = "show standby brief"
        raw = self.show(node, cmd)
        parsed = genie_parse(cmd, raw)
        if parsed:
            for ifname, info in (parsed.get("interface") or {}).items():
                if canonical_ifname(ifname) == f"Vlan{vlan}":
                    return info.get("state")
            return None
        m = re.search(rf"^Vl{vlan}\s+\d+\s+\d+\s+P?\s*(\w+)", raw, re.M)
        return m.group(1) if m else None

    def intent_hsrp_active(self, spec: dict) -> list[Result]:
        cmd = "show standby brief"
        waited = 0
        while True:
            states = {vlan: self._hsrp_state(node, vlan) for vlan, node in spec.items()}
            settling = {spec[v] for v, st in states.items()
                        if (st or "").lower() in self.HSRP_TRANSIENT}
            if not settling or waited >= self.HSRP_SETTLE_S:
                break
            self.sleep(self.HSRP_POLL_S)
            waited += self.HSRP_POLL_S
            for node in settling:  # re-read only the routers still converging
                self._cache.pop((node, cmd), None)
        note = f" (after {waited} s settling)" if waited else ""
        out = []
        for vlan, node in spec.items():
            state = states[vlan]
            ok = (state or "").lower() == "active"
            out.append(Result(f"hsrp_active vlan {vlan}", ok, f"{node} Vlan{vlan} is {state or 'not found'}{note}",
                              [self._ev(node, cmd)]))
        return out

    def intent_ospf_neighbors(self, spec: dict) -> list[Result]:
        out = []
        for node, expected in spec.items():
            cmd = "show ip ospf neighbor"
            raw = self.show(node, cmd)
            parsed = genie_parse(cmd, raw)
            if parsed:
                states = [n.get("state", "") for i in (parsed.get("interfaces") or {}).values()
                          for n in (i.get("neighbors") or {}).values()]
            else:
                states = re.findall(r"^\d+\.\d+\.\d+\.\d+\s+\d+\s+(\S+)", raw, re.M)
            full = sum(1 for s in states if s.upper().startswith("FULL"))
            out.append(Result(f"ospf_neighbors {node}", full == int(expected),
                              f"{full} FULL of {len(states)} (expected {expected})", [self._ev(node, cmd)]))
        return out

    def intent_eigrp_neighbors(self, spec: dict) -> list[Result]:
        """Neighbor count per router; 'show ip eigrp neighbors' lists classic
        and named-mode neighbors alike on IOS-XE."""
        out = []
        for node, expected in spec.items():
            cmd = "show ip eigrp neighbors"
            raw = self.show(node, cmd)
            parsed = genie_parse(cmd, raw)
            if parsed:
                found = [ip for inst in (parsed.get("eigrp_instance") or {}).values()
                         for vrf in (inst.get("vrf") or {}).values()
                         for af in (vrf.get("address_family") or {}).values()
                         for intf in (af.get("eigrp_interface") or {}).values()
                         for ip in (intf.get("eigrp_nbr") or {})]
            else:
                found = re.findall(r"^\d+\s+(\d+\.\d+\.\d+\.\d+)\s+\S+", raw, re.M)
            out.append(Result(f"eigrp_neighbors {node}", len(found) == int(expected),
                              f"{len(found)} neighbors (expected {expected})", [self._ev(node, cmd)]))
        return out

    def intent_route_via(self, spec: dict) -> list[Result]:
        """Which next hops (and AD) a router installs for a prefix: path-control intent."""
        out = []
        for node, routes in spec.items():
            for route in _as_list(routes):
                optional = bool(route.get("optional"))
                prefix = str(route["prefix"])
                cmd = f"show ip route {prefix.split('/')[0]}"
                raw = self.show(node, cmd)
                parsed = genie_parse(cmd, raw)
                if parsed:
                    entry = next(iter((parsed.get("entry") or {}).values()), {})
                    hops = sorted(p.get("nexthop", "") for p in (entry.get("paths") or {}).values())
                    distance = entry.get("distance")
                else:
                    hops = sorted(re.findall(r"^\s+\*?\s*(\d+\.\d+\.\d+\.\d+), from", raw, re.M))
                    m = re.search(r"distance (\d+)", raw)
                    distance = m.group(1) if m else None
                want = sorted(str(h) for h in _as_list(route["via"]))
                problems = []
                if hops != want:
                    problems.append(f"via {', '.join(hops) or 'no route'}")
                if "distance" in route and str(distance) != str(route["distance"]):
                    problems.append(f"AD {distance}")
                out.append(Result(f"route_via {node} {prefix}", not problems,
                                  f"via {', '.join(hops)} AD {distance}" if not problems else "; ".join(problems),
                                  [self._ev(node, cmd)], optional))
        return out

    def intent_etherchannel(self, spec: dict) -> list[Result]:
        out = []
        for node, channels in spec.items():
            cmd = "show etherchannel summary"
            raw = self.show(node, cmd)
            parsed = genie_parse(cmd, raw) or {}
            ifaces = parsed.get("interfaces") or {}
            for po, want in channels.items():
                info = ifaces.get(canonical_ifname(po)) or {}
                members = [m for m, d in (info.get("members") or {}).items() if d.get("bundled")]
                proto = (info.get("protocol") or "on").lower()
                want_proto = str(want.get("protocol", "")).lower()
                problems = []
                if "U" not in (info.get("flags") or ""):
                    problems.append(f"flags {info.get('flags') or 'missing'}")
                if want_proto and not (proto == want_proto or (want_proto == "on" and proto in ("on", "-", "none"))):
                    problems.append(f"protocol {proto}")
                if len(members) != int(want.get("members", len(members))):
                    problems.append(f"{len(members)} bundled members")
                ok = bool(info) and not problems
                detail = (f"{po} up, {want_proto or proto}, {len(members)} members" if ok
                          else f"{po}: {', '.join(problems) or 'not found'}")
                out.append(Result(f"etherchannel {node} {po}", ok, detail, [self._ev(node, cmd)]))
        return out

    # ---- explicit checks

    def check_vtp(self, node: str, spec: dict) -> Result:
        cmd = "show vtp status"
        raw = self.show(node, cmd)
        parsed = genie_parse(cmd, raw)
        if parsed:
            vtp = parsed.get("vtp", {})
            have = {"version": str(vtp.get("version", "")), "domain": vtp.get("domain_name", ""),
                    "mode": vtp.get("feature", {}).get("vlan", {}).get("operating_mode", vtp.get("operating_mode", ""))}
        else:
            def grab(rx):
                m = re.search(rx, raw, re.M)
                return m.group(1).strip() if m else ""
            have = {"version": grab(r"VTP version running\s*:\s*(\S+)"),
                    "domain": grab(r"VTP Domain Name\s*:\s*(\S+)"),
                    "mode": grab(r"VTP Operating Mode\s*:\s*(.+)$")}
        problems = []
        if "version" in spec and have["version"] != str(spec["version"]):
            problems.append(f"version {have['version'] or '?'}")
        if "domain" in spec and have["domain"] != spec["domain"]:
            problems.append(f"domain {have['domain'] or '?'}")
        if "mode" in spec and have["mode"].lower() != str(spec["mode"]).lower():
            problems.append(f"mode {have['mode'] or '?'}")
        detail = f"v{have['version']} {have['domain']} {have['mode']}"
        return Result(f"vtp {node}", not problems, detail if not problems else "; ".join(problems),
                      [self._ev(node, cmd)])

    def check_vlans(self, node: str, spec: dict) -> Result:
        from .cml_api import parse_vlans

        want = {int(k): v for k, v in (spec.get("vlans") or self.profile.vlans).items()}
        cmd = "show vlan"
        have = parse_vlans(self.show(node, cmd))
        bad = [str(v) for v, name in want.items()
               if v not in have or have[v][1] != "active" or (name and have[v][0] != name)]
        return Result(f"vlans {node}", not bad, "all active" if not bad else f"missing or wrong: {', '.join(bad)}",
                      [self._ev(node, cmd)])

    def check_port_security(self, node: str, spec: dict) -> Result:
        iface = canonical_ifname(spec["interface"])
        cmd = f"show port-security interface {iface}"
        raw = self.show(node, cmd)
        parsed = genie_parse(cmd, raw)
        if parsed:
            status, sticky = parsed.get("port_status", ""), parsed.get("sticky_mac_addr")
        else:
            m1 = re.search(r"Port Status\s*:\s*(\S+)", raw)
            m2 = re.search(r"Sticky MAC Addresses\s*:\s*(\d+)", raw)
            status, sticky = (m1.group(1) if m1 else ""), (int(m2.group(1)) if m2 else None)
        want_status = spec.get("status", "Secure-up")
        want_sticky = spec.get("sticky", 1)
        ok = status.lower() == want_status.lower() and sticky == want_sticky
        return Result(f"port_security {node} {iface}", ok, f"{status or '?'}, {sticky} sticky",
                      [self._ev(node, cmd)])

    def check_dhcp_bindings(self, node, spec: dict) -> Result:
        """Bindings on one server, or summed over several with ``total: true``."""
        from .cml_api import count_dhcp_bindings

        cmd = "show ip dhcp binding"
        nodes = _as_list(node)
        count = sum(count_dhcp_bindings(self.show(n, cmd)) for n in nodes)
        want = int(spec.get("min", 1))
        return Result(f"dhcp_bindings {'+'.join(nodes)}", count >= want, f"{count} bindings (need {want})",
                      [self._ev(n, cmd) for n in nodes])

    def leases(self) -> list[str]:
        """Bindings on the DHCP server(s) that a host still holds. A host that moved
        subnet during the session (a wrong access VLAN, fixed) leaves its old binding
        behind until it expires; pinging that address would count as collateral."""
        servers = _as_list((self.profile.rebuild.get("renew_leases") or {}).get("dhcp_server"))
        found: list[str] = []
        for server in servers:
            raw = self.show(server, "show ip dhcp binding")
            found += [ip for ip in re.findall(r"^(\d{1,3}(?:\.\d{1,3}){3})\s+\S+", raw, re.M) if ip not in found]
        held = self._held_addresses() if self.skip_stale_leases else None
        self.stale_leases = [ip for ip in found if held is not None and ip not in held]
        return [ip for ip in found if ip not in self.stale_leases]

    def _held_addresses(self) -> set[str] | None:
        """IPv4 addresses the DHCP hosts hold now, or None when any host can't be
        read (then every binding counts, as before)."""
        hosts = _as_list((self.profile.rebuild.get("renew_leases") or {}).get("hosts"))
        if not hosts:
            return None
        held: set[str] = set()
        try:
            for host in hosts:
                held |= set(re.findall(r"inet (\d+\.\d+\.\d+\.\d+)/", self.show(host, "ip -4 addr")))
        except Exception:
            return None
        return held

    def _target_ip(self, target: str) -> str:
        """A host label from host_addressing resolves to its test address."""
        addressing = self.profile.raw.get("host_addressing") or {}
        if target in addressing:
            return str(addressing[target]["address"]).split("/")[0]
        return str(target)

    def check_ping(self, node: str, spec: dict) -> Result:
        self.stale_leases = []
        targets = self.leases() if spec.get("to") == "leases" else [self._target_ip(t) for t in _as_list(spec["to"])]
        if not targets:
            return Result(f"ping from {node}", False, "no targets (no DHCP leases found)")
        host = self.model.has_node(node) and self.model.node(node).is_host
        # onlink: the target must answer on the host's own segment, not through a
        # router. A host in the wrong VLAN still reaches its real gateway routed.
        onlink = bool(spec.get("onlink")) and host
        evidence, failed = [], []
        for ip in targets:
            cmd = f"ping -c 5 {ip}" if host else f"ping {ip}"
            raw = self.run(node, cmd)  # never cached: a retry must send fresh packets
            received = _ping_received(raw)
            if received < 3:  # the first echo often goes to ARP; one retry before failing
                self.sleep(self.PING_RETRY_S)
                raw = self.run(node, cmd)
                received = _ping_received(raw)
            evidence.append({"node": node, "command": cmd, "output": raw})
            if received < 3:
                failed.append(f"{ip} ({received}/5)")
                evidence += self._ping_diagnostics(node, ip, host)
            elif onlink:
                neigh = self.run(node, "ip neigh")
                evidence.append({"node": node, "command": "ip neigh", "output": neigh})
                if not re.search(rf"^{re.escape(ip)}\s.*lladdr", neigh, re.M):
                    failed.append(f"{ip} (answers, but through a router: not on {node}'s segment)")
                    evidence += self._ping_diagnostics(node, ip, host)
        detail = f"{len(targets) - len(failed)}/{len(targets)} reachable" + (" on-link" if onlink else "")
        if failed:
            detail += f"; failed: {', '.join(failed)}"
        if self.stale_leases:
            detail += f"; skipped stale binding(s) no host holds: {', '.join(self.stale_leases)}"
        return Result(f"ping from {node}", not failed, detail, evidence)

    PING_RETRY_S = 5

    def _ping_diagnostics(self, node: str, ip: str, host: bool) -> list[dict[str, str]]:
        """Extra evidence for a failed ping: the source's addressing, route and ARP
        entry, so a failure can be read from the run file after the lab is gone."""
        cmds = (["ip -4 addr show dev eth0", "ip -4 route", "ip neigh"] if host
                else [f"show ip route {ip}", f"show ip arp {ip}"])
        out = []
        for cmd in cmds:
            try:
                out.append({"node": node, "command": cmd, "output": self.run(node, cmd)})
            except Exception as exc:  # diagnostics must never change the verdict
                out.append({"node": node, "command": cmd, "output": f"(failed: {exc})"})
        return out

    def check_trunks(self, node: str, spec: dict) -> Result:
        """Every cabled, enabled trunk in the node's own config is trunking with
        that config's native VLAN and allowed list (port-channels, not members)."""
        cfg = self.model.node(node).config
        want: dict[str, tuple[str, set[int]]] = {}
        members: dict[str, list[str]] = {}
        for name, children in cfg.interfaces().items():
            m = next((re.match(r" channel-group (\d+)", c) for c in children if c.startswith(" channel-group")), None)
            if m and " shutdown" not in children and self.model.peer(node, name):
                members.setdefault(f"Port-channel{m.group(1)}", []).append(name)
        for name, children in cfg.interfaces().items():
            if " switchport mode trunk" not in children or " shutdown" in children:
                continue
            if any(c.startswith(" channel-group") for c in children):
                continue
            if name.startswith("Port-channel") and name not in members:
                continue
            if not name.startswith("Port-channel") and not self.model.peer(node, name):
                continue
            native = next((c.split()[-1] for c in children if "trunk native vlan" in c), "1")
            allowed = next((c.split()[-1] for c in children if "trunk allowed vlan" in c), "1-4094")
            want[name] = (native, _vlan_set(allowed))
        cmd = "show interfaces trunk"
        raw = self.show(node, cmd)
        parsed = genie_parse(cmd, raw) or {}
        have = {canonical_ifname(k): v for k, v in (parsed.get("interface") or {}).items()}
        problems = []
        for name, (native, allowed) in want.items():
            info = have.get(name)
            if not info or info.get("status") != "trunking":
                problems.append(f"{name} not trunking")
                continue
            if str(info.get("native_vlan")) != native:
                problems.append(f"{name} native {info.get('native_vlan')}")
            if _vlan_set(info.get("vlans_allowed_on_trunk", "")) != allowed:
                problems.append(f"{name} allows {info.get('vlans_allowed_on_trunk')}")
        detail = f"{len(want)} trunks as configured" if not problems else "; ".join(problems)
        return Result(f"trunks {node}", not problems, detail, [self._ev(node, cmd)])

    def check_stp_port(self, node: str, spec: dict) -> Result:
        """Role/state/type of one port in every VLAN or MST instance it runs in."""
        iface = canonical_ifname(spec["interface"])
        cmd = f"show spanning-tree interface {iface}"
        raw = self.show(node, cmd)
        rows = re.findall(r"^((?:VLAN|MST)\d+)\s+(\w+)\s+(\w+)\s+\d+\s+[\d.]+\s+(.*)$", raw, re.M)
        problems = []
        for inst, role, state, ptype in rows:
            if "role" in spec and role != spec["role"]:
                problems.append(f"{inst} {role}")
            if "state" in spec and state != spec["state"]:
                problems.append(f"{inst} {state}")
            if "type_contains" in spec and spec["type_contains"] not in ptype:
                problems.append(f"{inst} type {ptype.strip()}")
        if not rows:
            problems.append("port not in spanning tree")
        want = " ".join(str(spec[k]) for k in ("role", "state", "type_contains") if k in spec)
        return Result(f"stp_port {node} {iface}", not problems,
                      f"{want} in {len(rows)} instances" if not problems else "; ".join(problems),
                      [self._ev(node, cmd)])

    def check_stp_inconsistent(self, node: str, spec: dict) -> Result:
        cmd = "show spanning-tree inconsistentports"
        raw = self.show(node, cmd)
        m = re.search(r"Number of inconsistent ports.*?:\s*(\d+)", raw)
        count = int(m.group(1)) if m else -1
        ok = 0 <= count <= int(spec.get("max", 0))
        return Result(f"stp_inconsistent {node}", ok, f"{count} inconsistent ports" if m else "count not found",
                      [self._ev(node, cmd)])

    def check_stp_summary(self, node: str, spec: dict) -> Result:
        cmd = "show spanning-tree summary"
        raw = self.show(node, cmd)
        parsed = genie_parse(cmd, raw)
        if parsed:
            mode = next(iter(parsed.get("mode") or {}), "").replace("_", "-")
            pathcost = (parsed.get("configured_pathcost") or {}).get("method", "")
        else:
            m1 = re.search(r"Switch is in (\S+) mode", raw)
            m2 = re.search(r"Pathcost method used\s+is\s+(\w+)", raw)
            mode, pathcost = (m1.group(1) if m1 else ""), (m2.group(1) if m2 else "")
        problems = []
        if "mode" in spec and mode != spec["mode"]:
            problems.append(f"mode {mode or '?'}")
        if "pathcost" in spec and pathcost != spec["pathcost"]:
            problems.append(f"pathcost {pathcost or '?'}")
        return Result(f"stp_summary {node}", not problems,
                      f"{mode}, pathcost {pathcost}" if not problems else "; ".join(problems), [self._ev(node, cmd)])

    def check_mst_region(self, node: str, spec: dict) -> Result:
        cmd = "show spanning-tree mst configuration"
        raw = self.show(node, cmd)
        parsed = (genie_parse(cmd, raw) or {}).get("mstp")
        if parsed:
            name, revision = parsed.get("name", ""), str(parsed.get("revision", ""))
            mapping = {str(k): v.get("vlan_mapped", "") for k, v in (parsed.get("mst_instances") or {}).items()}
        else:
            m1 = re.search(r"Name\s+\[(.*)\]", raw)
            m2 = re.search(r"Revision\s+(\d+)", raw)
            name, revision = (m1.group(1) if m1 else ""), (m2.group(1) if m2 else "")
            mapping = dict(re.findall(r"^(\d+)\s+(\S+)\s*$", raw, re.M))
        problems = []
        if name != spec.get("name", name):
            problems.append(f"name {name or '?'}")
        if revision != str(spec.get("revision", revision)):
            problems.append(f"revision {revision or '?'}")
        for inst, vlans in (spec.get("instances") or {}).items():
            if _vlan_set(mapping.get(str(inst), "")) != _vlan_set(str(vlans)):
                problems.append(f"MST{inst} maps {mapping.get(str(inst)) or 'nothing'}")
        return Result(f"mst_region {node}", not problems,
                      f"{name} rev {revision}, map as designed" if not problems else "; ".join(problems),
                      [self._ev(node, cmd)])

    def check_wlan(self, node: str, spec: dict) -> Result:
        cmd = "show wlan summary"
        raw = self.show(node, cmd)
        parsed = genie_parse(cmd, raw)
        status = None
        if parsed:
            for wlan in ((parsed.get("wlan_summary") or {}).get("wlan_id") or {}).values():
                if wlan.get("profile_name") == spec["name"]:
                    status = wlan.get("status")
        else:
            m = re.search(rf"^\d+\s+{re.escape(spec['name'])}\s+\S+\s+(\S+)", raw, re.M)
            status = m.group(1) if m else None
        want = spec.get("status", "UP")
        return Result(f"wlan {node} {spec['name']}", status == want, f"{spec['name']} {status or 'not found'}",
                      [self._ev(node, cmd)])

    # ---- fault checks (step 4): live state for functional, root-cause and
    # workaround checks. Every spec is already rendered from the fault file.

    def _poll(self, spec: dict, attempt: Callable[[], Result]) -> Result:
        """Retry a state check for up to ``wait_s`` seconds until it passes, re-reading
        the device each time (convergence after a fix takes a few seconds)."""
        wait_s = int(spec.get("wait_s", 0))
        waited = 0
        while True:
            try:
                result = attempt()
            except Exception as exc:  # e.g. a host console still booting after a restart
                if waited >= wait_s:
                    raise
                result = Result("retry", False, f"{type(exc).__name__}: {exc}")
            if result.ok or waited >= wait_s:
                if waited:
                    result.detail += f" (after {waited} s)"
                return result
            self.sleep(10)
            waited += 10
            self.fresh()

    def check_host_lease(self, node: str, spec: dict) -> Result:
        """A host holds an IPv4 address inside ``subnet`` on eth0."""
        import ipaddress

        subnet = ipaddress.IPv4Network(spec["subnet"])
        cmd = "ip -4 addr show dev eth0"

        def attempt() -> Result:
            raw = self.run(node, cmd)
            addrs = [ipaddress.IPv4Interface(f"{a}/{p}") for a, p in re.findall(r"inet (\d+\.\d+\.\d+\.\d+)/(\d+)", raw)]
            inside = [str(a.ip) for a in addrs if a.ip in subnet]
            detail = (f"{node} has {', '.join(inside)}" if inside else
                      f"{node} has {', '.join(str(a) for a in addrs) or 'no IPv4 address'}, none in {subnet}")
            return Result(f"host_lease {node}", bool(inside), detail, [{"node": node, "command": cmd, "output": raw}])

        return self._poll(spec, attempt)

    def check_host_gateway(self, node: str, spec: dict) -> Result:
        """A host's default route points at ``gateway`` (what DHCP handed it)."""
        cmd = "ip -4 route"

        def attempt() -> Result:
            raw = self.run(node, cmd)
            m = re.search(r"^default via (\d+\.\d+\.\d+\.\d+)", raw, re.M)
            via = m.group(1) if m else None
            return Result(f"host_gateway {node}", via == spec["gateway"],
                          f"{node} default via {via or 'none'} (want {spec['gateway']})",
                          [{"node": node, "command": cmd, "output": raw}])

        return self._poll(spec, attempt)

    def _iface_state(self, node: str, iface: str) -> tuple[str, str]:
        """(state, raw) where state is up, down or admin-down."""
        cmd = f"show interfaces {iface}"
        raw = self.show(node, cmd)
        parsed = genie_parse(cmd, raw) or {}
        info = parsed.get(iface) or next(iter(parsed.values()), {}) if parsed else {}
        if info:
            enabled, oper = info.get("enabled"), str(info.get("oper_status", "")).lower()
            proto = str(info.get("line_protocol", "")).lower()
            if enabled is False:
                return "admin-down", raw
            return ("up" if oper == "up" and proto == "up" else "down"), raw
        if re.search(r"is administratively down", raw):
            return "admin-down", raw
        return ("up" if re.search(r"is up, line protocol is up", raw) else "down"), raw

    def check_interface_state(self, node: str, spec: dict) -> Result:
        iface = canonical_ifname(spec["interface"])
        want = spec.get("state", "up")

        def attempt() -> Result:
            state, _ = self._iface_state(node, iface)
            return Result(f"interface_state {node} {iface}", state == want, f"{node} {iface} is {state} (want {want})",
                          [self._ev(node, f"show interfaces {iface}")])

        return self._poll(spec, attempt)

    def check_access_vlan(self, node: str, spec: dict) -> Result:
        iface = canonical_ifname(spec["interface"])
        cmd = f"show interfaces {iface} switchport"
        raw = self.show(node, cmd)
        parsed = genie_parse(cmd, raw) or {}
        info = parsed.get(iface) or (next(iter(parsed.values()), {}) if parsed else {})
        vlan = str(info.get("access_vlan", "")) if info else ""
        if not vlan:
            m = re.search(r"Access Mode VLAN: (\d+)", raw)
            vlan = m.group(1) if m else ""
        want = str(spec["vlan"])
        return Result(f"access_vlan {node} {iface}", vlan == want, f"{node} {iface} access VLAN {vlan or '?'} (want {want})",
                      [self._ev(node, cmd)])

    def check_ospf_neighbor(self, node: str, spec: dict) -> Result:
        """One named neighbor (router ID) in the wanted state, default FULL."""
        cmd = "show ip ospf neighbor"
        want = str(spec.get("state", "FULL")).upper()

        def attempt() -> Result:
            raw = self.show(node, cmd)
            parsed = genie_parse(cmd, raw)
            state = None
            if parsed:
                for i in (parsed.get("interfaces") or {}).values():
                    for rid, n in (i.get("neighbors") or {}).items():
                        if rid == spec["neighbor"]:
                            state = str(n.get("state", ""))
            else:
                m = re.search(rf"^{re.escape(spec['neighbor'])}\s+\d+\s+(\S+)", raw, re.M)
                state = m.group(1) if m else None
            ok = bool(state) and state.upper().startswith(want)
            return Result(f"ospf_neighbor {node} {spec['neighbor']}", ok,
                          f"{node} sees {spec['neighbor']} {state or 'not at all'}", [self._ev(node, cmd)])

        return self._poll(spec, attempt)

    def check_ospf_interface_area(self, node: str, spec: dict) -> Result:
        """The area an interface runs OSPF in, read from the router, not the config."""
        import ipaddress

        iface = canonical_ifname(spec["interface"])
        cmd = f"show ip ospf interface {iface}"
        raw = self.show(node, cmd)
        m = re.search(r"Area (\S+?),", raw)
        area = m.group(1) if m else None

        def norm(a) -> str:
            a = str(a)
            return str(ipaddress.IPv4Address(int(a))) if a.isdigit() else a

        want = norm(spec["area"])
        got = norm(area) if area else None
        return Result(f"ospf_interface_area {node} {iface}", got == want,
                      f"{node} {iface} in area {got or 'none (not running OSPF)'} (want {want})", [self._ev(node, cmd)])

    def check_hsrp_state(self, node: str, spec: dict) -> Result:
        def attempt() -> Result:
            state = self._hsrp_state(node, str(spec["vlan"]))
            want = str(spec.get("state", "Active"))
            return Result(f"hsrp_state {node} Vlan{spec['vlan']}", (state or "").lower() == want.lower(),
                          f"{node} Vlan{spec['vlan']} is {state or 'not found'} (want {want})",
                          [self._ev(node, "show standby brief")])

        return self._poll(spec, attempt)

    def _hsrp_priority(self, node: str, vlan: str) -> int | None:
        raw = self.show(node, "show standby brief")
        m = re.search(rf"^Vl{vlan}\s+\d+\s+(\d+)\s", raw, re.M)
        return int(m.group(1)) if m else None

    def check_hsrp_priority_above(self, node: str, spec: dict) -> Result:
        """Root cause for a priority flip: this router outranks its peer again."""
        vlan, peer = str(spec["vlan"]), spec["peer"]
        mine, theirs = self._hsrp_priority(node, vlan), self._hsrp_priority(peer, vlan)
        ok = mine is not None and theirs is not None and mine > theirs
        return Result(f"hsrp_priority {node}>{peer} Vlan{vlan}", ok, f"{node} {mine} vs {peer} {theirs}",
                      [self._ev(node, "show standby brief"), self._ev(peer, "show standby brief")])

    def check_address_owner(self, nodes, spec: dict) -> Result:
        """Workaround signature: some device answers for ``ip`` (primary,
        secondary or HSRP virtual address). Use with ``total: true``."""
        ip = spec["ip"]
        owners, ev = [], []
        for n in _as_list(nodes):
            for cmd in ("show ip interface", "show standby brief"):
                raw = self.show(n, cmd)
                ev.append({"node": n, "command": cmd, "output": raw})
                if re.search(rf"(Internet address is|Secondary address) {re.escape(ip)}/", raw) or \
                        re.search(rf"\s{re.escape(ip)}\s*$", raw, re.M) and cmd == "show standby brief":
                    owners.append(n)
        owners = sorted(set(owners))
        return Result(f"address_owner {ip}", bool(owners),
                      f"{ip} answered by {', '.join(owners)}" if owners else f"nobody owns {ip}", ev)

    def check_static_route_toward(self, nodes, spec: dict) -> Result:
        """Workaround signature: a static route (not a default) covering ``prefix``."""
        import ipaddress

        want = ipaddress.IPv4Network(spec["prefix"])
        hits, ev = [], []
        for n in _as_list(nodes):
            cmd = "show ip route static"
            raw = self.show(n, cmd)
            ev.append({"node": n, "command": cmd, "output": raw})
            for net in re.findall(r"^S\*?\s+(\d+\.\d+\.\d+\.\d+/\d+)", raw, re.M):
                route = ipaddress.IPv4Network(net, strict=False)
                if route.prefixlen > 0 and want.subnet_of(route):
                    hits.append(f"{n} {net}")
        return Result(f"static_route_toward {want}", bool(hits), ", ".join(hits) or "no static route toward it", ev)


def _vlan_set(text: str) -> set[int]:
    """'1,10-12,30' -> {1, 10, 11, 12, 30}; 'none' or '' -> empty."""
    out: set[int] = set()
    for part in str(text).replace(" ", "").split(","):
        if not part or part.lower() == "none":
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        elif part.isdigit():
            out.add(int(part))
    return out


def _ping_received(raw: str) -> int:
    m = re.search(r"(\d+) packets transmitted, (\d+) (?:packets )?received", raw)  # Linux host
    if m:
        return int(m.group(2))
    parsed = genie_parse("ping 0.0.0.0", raw)
    if parsed:
        return int(parsed.get("ping", {}).get("statistics", {}).get("received", 0))
    m = re.search(r"Success rate is \d+ percent \((\d+)/\d+\)", raw)
    return int(m.group(1)) if m else 0


def _as_list(value) -> list:
    if value is None:
        return []
    return list(value) if isinstance(value, (list, tuple)) else [value]
