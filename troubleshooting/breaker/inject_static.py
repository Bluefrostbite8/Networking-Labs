"""Static faults: edits to the topology YAML before import.

A fault file names a ``target`` (where the fault can go) and an ``action`` (what
it changes). Targets read the healthy lab and return every place the fault fits,
each as a dict of template variables ({switch}, {iface}, {pid}, ...). Actions
change one copy of the lab and return what they touched, so the grader can check
persistence and compare against the original sections.

Every value comes from the lab's own config or its profile; nothing is invented.
Where a fault needs a "wrong" value (a bad gateway, a bad area), the action picks
it from the lab's own address plan by a fixed rule and records why.
"""

from __future__ import annotations

import ipaddress
import random
import re
from typing import Any, Callable

from .lab_model import IosConfig, LabModel, LabModelError, LabProfile, canonical_ifname

Vars = dict[str, Any]


class InjectError(LabModelError):
    """A fault could not be applied to this lab as written."""


# --------------------------------------------------------------------------- helpers


def short_ifname(name: str) -> str:
    """GigabitEthernet0/0 -> Gi0/0 (how tickets and show commands usually name it)."""
    for long, short in (("GigabitEthernet", "Gi"), ("FastEthernet", "Fa"), ("Ethernet", "Et"),
                        ("Port-channel", "Po"), ("Loopback", "Lo")):
        if name.startswith(long):
            return short + name[len(long):]
    return name


def _primary_ip(children: list[str]) -> ipaddress.IPv4Interface | None:
    for c in children:
        m = re.match(r"^ ip address (\d+\.\d+\.\d+\.\d+) (\d+\.\d+\.\d+\.\d+)$", c)
        if m:
            return ipaddress.IPv4Interface(f"{m.group(1)}/{m.group(2)}")
    return None


def _hsrp_groups(children: list[str]) -> dict[str, dict[str, Any]]:
    """{group: {vip, priority, preempt}} from one interface section."""
    groups: dict[str, dict[str, Any]] = {}
    for c in children:
        m = re.match(r"^ standby (\d+) (ip|priority|preempt)\s*(\S*)", c)
        if not m:
            continue
        g = groups.setdefault(m.group(1), {"vip": None, "priority": 100, "preempt": False})
        if m.group(2) == "ip":
            g["vip"] = m.group(3)
        elif m.group(2) == "priority":
            g["priority"] = int(m.group(3))
        else:
            g["preempt"] = True
    return {k: v for k, v in groups.items() if v["vip"]}


def vlan_subnets(model: LabModel) -> dict[int, dict[str, str]]:
    """{vlan: {subnet, gateway}} from the SVIs in the lab. The gateway is the HSRP
    virtual IP when the SVI runs HSRP, else the SVI address."""
    out: dict[int, dict[str, str]] = {}
    for node in model.ios_nodes():
        for name, children in node.config.interfaces().items():
            m = re.match(r"^Vlan(\d+)$", name)
            ip = _primary_ip(children)
            if not m or ip is None:
                continue
            vlan = int(m.group(1))
            groups = _hsrp_groups(children)
            gateway = next(iter(groups.values()))["vip"] if groups else str(ip.ip)
            entry = out.setdefault(vlan, {"subnet": str(ip.network), "gateway": gateway})
            if groups:  # prefer the VIP when any SVI in the VLAN runs HSRP
                entry["gateway"] = gateway
    return out


def used_addresses(model: LabModel) -> set[str]:
    """Every interface address, secondary address and HSRP VIP in the lab."""
    used: set[str] = set()
    for node in model.ios_nodes():
        for children in node.config.interfaces().values():
            for c in children:
                m = re.match(r"^ ip address (\d+\.\d+\.\d+\.\d+) ", c)
                if m:
                    used.add(m.group(1))
            used |= {g["vip"] for g in _hsrp_groups(children).values()}
    return used


def _wildcard_covers(net: str, wild: str, ip: str) -> bool:
    n = int(ipaddress.IPv4Address(net))
    w = int(ipaddress.IPv4Address(wild))
    return (int(ipaddress.IPv4Address(ip)) & ~w & 0xFFFFFFFF) == (n & ~w & 0xFFFFFFFF)


def _dhcp_hosts(profile: LabProfile) -> list[str]:
    return list((profile.rebuild.get("renew_leases") or {}).get("hosts") or [])


# --------------------------------------------------------------------------- targets


def target_host_access_port(model: LabModel, profile: LabProfile) -> list[Vars]:
    """Every host cabled to a switch access port in a VLAN that has a gateway."""
    subnets = vlan_subnets(model)
    dhcp = set(_dhcp_hosts(profile))
    out = []
    # DHCP clients only: the functional checks prove the fix by the host leasing
    # an address in its own subnet again (static test hosts would need others).
    for host in sorted(dhcp):
        for link in model.links_of(host):
            end = link.other(host)
            node = model.node(end.node)
            if not node.is_l2_switch or end.node in profile.never_touch:
                continue
            iface = canonical_ifname(end.interface)
            children = node.config.interface(iface) or []
            if " switchport mode access" not in children:
                continue
            m = next((re.match(r"^ switchport access vlan (\d+)$", c) for c in children
                      if c.startswith(" switchport access vlan")), None)
            vlan = int(m.group(1)) if m else 1
            if vlan not in subnets:
                continue
            out.append({"switch": end.node, "iface": iface, "iface_short": short_ifname(iface),
                        "host": host, "vlan": vlan, "subnet": subnets[vlan]["subnet"],
                        "gateway": subnets[vlan]["gateway"], "dhcp": True})
    return out


def target_dhcp_pool(model: LabModel, profile: LabProfile) -> list[Vars]:
    """DHCP pools with a default-router whose subnet holds at least one DHCP host."""
    hosts_by_subnet: dict[str, list[str]] = {}
    for t in target_host_access_port(model, profile):
        if t["dhcp"]:
            hosts_by_subnet.setdefault(t["subnet"], []).append(t["host"])
    out = []
    for node in model.ios_nodes():
        cfg = node.config
        for s in cfg.find(r"ip dhcp pool \S"):
            net = next((re.match(r"^ network (\S+) (\S+)$", c) for c in s.children
                        if c.startswith(" network ")), None)
            gw = next((re.match(r"^ default-router (\S+)", c) for c in s.children
                       if c.startswith(" default-router ")), None)
            if not net or not gw:
                continue
            subnet = str(ipaddress.IPv4Network(f"{net.group(1)}/{net.group(2)}"))
            hosts = sorted(hosts_by_subnet.get(subnet, []))
            if not hosts:
                continue
            vlan = next((v for v, info in vlan_subnets(model).items() if info["subnet"] == subnet), None)
            # An address on the server outside the pool: reaching it needs the gateway.
            server_ip = next((str(ip.ip) for name, ch in sorted(cfg.interfaces().items())
                              if (ip := _primary_ip(ch)) and " shutdown" not in ch
                              and ip.ip not in ipaddress.IPv4Network(subnet)), None)
            # The router that answers for the gateway today: the HSRP design active, else the SVI owner.
            hsrp = (profile.raw.get("intent") or {}).get("hsrp_active") or {}
            gateway_node = hsrp.get(str(vlan)) or next(
                (n.label for n in model.ios_nodes() for name, ch in n.config.interfaces().items()
                 if (ip := _primary_ip(ch)) and str(ip.ip) == gw.group(1)), None)
            if not server_ip or not gateway_node:
                continue
            out.append({"server": node.label, "pool": s.header.split()[-1], "pool_header": s.header,
                        "network": net.group(1), "mask": net.group(2), "subnet": subnet,
                        "gateway": gw.group(1), "hosts": hosts, "host": hosts[0], "vlan": vlan,
                        "server_ip": server_ip, "gateway_node": gateway_node})
    return out


def _ospf_statements(cfg: IosConfig) -> list[dict[str, str]]:
    out = []
    for s in cfg.find(r"router ospf \d+"):
        pid = s.header.split()[-1]
        rid = next((c.split()[-1] for c in s.children if c.startswith(" router-id ")), None)
        for c in s.children:
            m = re.match(r"^ network (\S+) (\S+) area (\S+)$", c)
            if m:
                out.append({"pid": pid, "rid": rid, "net": m.group(1), "wild": m.group(2),
                            "area": m.group(3), "line": c, "header": s.header})
    return out


def target_ospf_link_end(model: LabModel, profile: LabProfile) -> list[Vars]:
    """One end of a cabled routed link between two OSPF routers, where a single
    network statement covers exactly that interface (so changing its area moves
    nothing else)."""
    out = []
    for link in model.links:
        ends = [(link.a, link.b), (link.b, link.a)]
        for here, there in ends:
            a, b = model.node(here.node), model.node(there.node)
            if not (a.is_ios and b.is_ios) or here.node in profile.never_touch or there.node in profile.never_touch:
                continue
            a_if, b_if = canonical_ifname(here.interface), canonical_ifname(there.interface)
            a_ip = _primary_ip(a.config.interface(a_if) or [])
            b_ip = _primary_ip(b.config.interface(b_if) or [])
            if not a_ip or not b_ip or a_ip.network != b_ip.network:
                continue
            a_st, b_st = _ospf_statements(a.config), _ospf_statements(b.config)
            mine = [s for s in a_st if _wildcard_covers(s["net"], s["wild"], str(a_ip.ip))]
            peer = [s for s in b_st if _wildcard_covers(s["net"], s["wild"], str(b_ip.ip))]
            if len(mine) != 1 or len(peer) != 1 or not mine[0]["rid"] or not peer[0]["rid"]:
                continue
            covered = [ip for ip in _all_ips(a.config) if _wildcard_covers(mine[0]["net"], mine[0]["wild"], ip)]
            if covered != [str(a_ip.ip)]:
                continue
            st, ps = mine[0], peer[0]
            out.append({"node": here.node, "iface": a_if, "iface_short": short_ifname(a_if),
                        "peer": there.node, "peer_iface": b_if, "peer_iface_short": short_ifname(b_if),
                        "pid": st["pid"], "net": st["net"], "wild": st["wild"], "original_area": st["area"],
                        "node_rid": st["rid"], "peer_rid": ps["rid"], "peer_pid": ps["pid"], "peer_ip": str(b_ip.ip),
                        "peer_net": ps["net"], "peer_wild": ps["wild"], "subnet": str(a_ip.network),
                        "ospf_header": st["header"], "peer_ospf_header": ps["header"]})
    return out


def _all_ips(cfg: IosConfig) -> list[str]:
    ips = []
    for children in cfg.interfaces().values():
        ip = _primary_ip(children)
        if ip:
            ips.append(str(ip.ip))
    return ips


def target_hsrp_design_active(model: LabModel, profile: LabProfile) -> list[Vars]:
    """Each VLAN whose HSRP active router is fixed by the profile's intent."""
    intent = (profile.raw.get("intent") or {}).get("hsrp_active") or {}
    out = []
    for vlan, node_label in sorted(intent.items(), key=lambda kv: int(kv[0])):
        svi = f"Vlan{vlan}"
        mine = _hsrp_groups(model.node(node_label).config.interface(svi) or [])
        for group, g in mine.items():
            for peer in model.ios_nodes():
                if peer.label == node_label:
                    continue
                theirs = _hsrp_groups(peer.config.interface(svi) or []).get(group)
                if not theirs or theirs["vip"] != g["vip"]:
                    continue
                # The fault adds preempt to the peer, so a peer that already preempts is
                # left out: its reference fix would otherwise remove part of the design.
                if g["priority"] <= theirs["priority"] or theirs["priority"] < 1 or theirs["preempt"]:
                    continue
                peer_ip = _primary_ip(peer.config.interface(svi) or [])
                out.append({"node": node_label, "peer": peer.label, "vlan": int(vlan), "svi": svi,
                            "peer_ip": str(peer_ip.ip) if peer_ip else "",
                            "group": group, "vip": g["vip"], "priority": g["priority"],
                            "peer_priority": theirs["priority"], "peer_preempt": theirs["preempt"],
                            "bad_priority": theirs["priority"] - 1})
    return out


TARGETS: dict[str, Callable[[LabModel, LabProfile], list[Vars]]] = {
    "host_access_port": target_host_access_port,
    "dhcp_pool_with_hosts": target_dhcp_pool,
    "ospf_network_on_link_end": target_ospf_link_end,
    "hsrp_design_active": target_hsrp_design_active,
}


# --------------------------------------------------------------------------- actions
# Each action edits ``model`` in place and returns
#   {"vars": {...extra template variables...},
#    "touched": [(node, section header), ...],
#    "changes": [{"node", "section", "before", "after"}]}


def _edit(model: LabModel, node: str, header: str, edit: Callable[[list[str]], list[str]]) -> dict:
    n = model.node(node)
    cfg = n.config
    s = cfg.section(header)
    if s is None:
        raise InjectError(f"{node}: section {header!r} not found")
    before = list(s.children)
    after = edit(list(before))
    cfg.replace_section(header, after)
    n.save_config(cfg)
    return {"node": node, "section": header, "before": before, "after": after}


def action_shutdown(model: LabModel, v: Vars, rng: random.Random) -> dict:
    header = f"interface {v['iface']}"

    def edit(children: list[str]) -> list[str]:
        children = [c for c in children if c.strip() != "no shutdown"]
        return children + [" shutdown"]

    ch = _edit(model, v["switch"], header, edit)
    return {"vars": {}, "touched": [(v["switch"], header)], "changes": [ch]}


def action_change_access_vlan(model: LabModel, v: Vars, rng: random.Random) -> dict:
    subnets = vlan_subnets(model)
    choices = sorted(vlan for vlan in subnets if vlan != v["vlan"])
    if not choices:
        raise InjectError(f"no other VLAN with a gateway for {v['switch']} {v['iface']}")
    bad = rng.choice(choices)
    header = f"interface {v['iface']}"

    def edit(children: list[str]) -> list[str]:
        out = [c for c in children if not c.startswith(" switchport access vlan ")]
        idx = next((i for i, c in enumerate(out) if c == " switchport mode access"), 0)
        out.insert(idx, f" switchport access vlan {bad}")
        return out

    ch = _edit(model, v["switch"], header, edit)
    return {"vars": {"bad_vlan": bad, "bad_subnet": subnets[bad]["subnet"]},
            "touched": [(v["switch"], header)], "changes": [ch]}


def action_change_default_router(model: LabModel, v: Vars, rng: random.Random) -> dict:
    """Bad gateway = the highest address of the pool's excluded range that no
    device in the lab uses: inside the subnet, never leased, answered by nobody."""
    server = model.node(v["server"]).config
    subnet = ipaddress.IPv4Network(v["subnet"])
    used = used_addresses(model)
    candidates: list[ipaddress.IPv4Address] = []
    for line in server.global_lines():
        m = re.match(r"^ip dhcp excluded-address (\S+)(?: (\S+))?$", line)
        if not m:
            continue
        lo = ipaddress.IPv4Address(m.group(1))
        hi = ipaddress.IPv4Address(m.group(2) or m.group(1))
        for i in range(int(lo), int(hi) + 1):
            addr = ipaddress.IPv4Address(i)
            if addr in subnet and str(addr) not in used and addr != subnet.network_address:
                candidates.append(addr)
    if not candidates:
        raise InjectError(f"{v['server']} pool {v['pool']}: no unused excluded address to use as a bad gateway")
    bad = str(max(candidates))

    def edit(children: list[str]) -> list[str]:
        return [f" default-router {bad}" if c.startswith(" default-router ") else c for c in children]

    ch = _edit(model, v["server"], v["pool_header"], edit)
    return {"vars": {"bad_gateway": bad}, "touched": [(v["server"], v["pool_header"])], "changes": [ch]}


def action_change_area(model: LabModel, v: Vars, rng: random.Random) -> dict:
    bad = "1" if v["original_area"] in ("0", "0.0.0.0") else "0"
    old = f" network {v['net']} {v['wild']} area {v['original_area']}"

    def edit(children: list[str]) -> list[str]:
        if old not in children:
            raise InjectError(f"{v['node']}: {old.strip()!r} not in {v['ospf_header']}")
        return [f" network {v['net']} {v['wild']} area {bad}" if c == old else c for c in children]

    ch = _edit(model, v["node"], v["ospf_header"], edit)
    return {"vars": {"bad_area": bad}, "touched": [(v["node"], v["ospf_header"])], "changes": [ch]}


def action_hsrp_priority_flip(model: LabModel, v: Vars, rng: random.Random) -> dict:
    """Design active drops below its peer, and the peer gets preempt so it takes
    over whichever SVI comes up first (VTP brings the two up seconds apart)."""
    header = f"interface {v['svi']}"
    g = v["group"]

    def lower(children: list[str]) -> list[str]:
        prio = f" standby {g} priority "
        out = [c for c in children if not c.startswith(prio)]
        idx = next((i for i, c in enumerate(out) if c.startswith(f" standby {g} ip ")), len(out) - 1)
        out.insert(idx + 1, f" standby {g} priority {v['bad_priority']}")
        return out

    changes = [_edit(model, v["node"], header, lower)]
    touched = [(v["node"], header)]
    if not v["peer_preempt"]:
        def add_preempt(children: list[str]) -> list[str]:
            return children + [f" standby {g} preempt"]

        changes.append(_edit(model, v["peer"], header, add_preempt))
        touched.append((v["peer"], header))
    return {"vars": {}, "touched": touched, "changes": changes}


ACTIONS: dict[str, Callable[[LabModel, Vars, random.Random], dict]] = {
    "shutdown": action_shutdown,
    "change_access_vlan": action_change_access_vlan,
    "change_default_router": action_change_default_router,
    "change_area": action_change_area,
    "hsrp_priority_flip": action_hsrp_priority_flip,
}
