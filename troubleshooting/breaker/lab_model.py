"""Offline model of a lab: topology.yaml, per-node config files, cabling and profile.

Nothing in this module talks to CML. It reads a CML 0.3.x export, exposes the
nodes, their IOS configuration as sections, and the cabling resolved from the
``links`` list, and writes the topology back out as importable YAML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml

# Repo layout: Networking-Labs/troubleshooting/breaker/lab_model.py
TOOL_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = TOOL_ROOT.parent
PROFILES_DIR = TOOL_ROOT / "labs"
LAB_DIR_RE = re.compile(r"^\d{2}-[a-z0-9-]+$")

# Which file in a node's ``configuration`` list holds its config, per node_definition.
# Checked against all six exports: every IOS-family node (iosvl2, iosv, iol-xe)
# uses ios_config.txt; only the Cat9800 uses iosxe_config.txt.
CONFIG_FILE_BY_DEFINITION = {
    "iosvl2": "ios_config.txt",
    "iosv": "ios_config.txt",
    "iol-xe": "ios_config.txt",
    "ioll2-xe": "ios_config.txt",
    "cat9800": "iosxe_config.txt",
    "desktop": "node.cfg",
    "wireless-ap": "user-data",
    "wireless-client": "user-data",
}

IOS_DEFINITIONS = {"iosvl2", "iosv", "iol-xe", "ioll2-xe"}
WLC_DEFINITIONS = {"cat9800"}
L2_SWITCH_DEFINITIONS = {"iosvl2", "ioll2-xe"}
HOST_DEFINITIONS = {"desktop", "wireless-ap", "wireless-client"}
# Never modified, never counted against the CML node licence.
INFRA_DEFINITIONS = {"external_connector", "unmanaged_switch"}

IFNAME_PREFIXES = {
    "gi": "GigabitEthernet",
    "te": "TenGigabitEthernet",
    "fa": "FastEthernet",
    "et": "Ethernet",
    "po": "Port-channel",
    "vl": "Vlan",
    "lo": "Loopback",
}


class LabModelError(ValueError):
    """The topology or profile can't be used as it is."""


def canonical_ifname(name: str) -> str:
    """Expand an abbreviated interface name: 'gi0/1' -> 'GigabitEthernet0/1'."""
    m = re.match(r"^([A-Za-z-]+)\s*([\d/.:]+)$", name.strip())
    if not m:
        return name.strip()
    word, num = m.groups()
    low = word.lower()
    if low in ("e", "eth"):
        return "Ethernet" + num
    for full in IFNAME_PREFIXES.values():
        if full.lower() == low:
            return full + num
    for short, full in IFNAME_PREFIXES.items():
        if low.startswith(short) and full.lower().startswith(low):
            return full + num
    return word + num


# --------------------------------------------------------------------------- config


@dataclass
class Section:
    """A top-level config line and the indented lines under it."""

    header: str
    children: list[str]
    start: int  # index of the header line in IosConfig.lines
    end: int  # index one past the last child


class IosConfig:
    """IOS / IOS-XE configuration text split into top-level sections.

    Unmodified text round-trips byte for byte through ``text``.
    """

    def __init__(self, text: str) -> None:
        self.lines: list[str] = text.split("\n")

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    def sections(self) -> list[Section]:
        out: list[Section] = []
        i, n = 0, len(self.lines)
        while i < n:
            line = self.lines[i]
            if not line.strip() or line.startswith((" ", "!")):
                i += 1
                continue
            j = i + 1
            while j < n and self.lines[j].startswith(" "):
                j += 1
            out.append(Section(line.rstrip(), [c.rstrip() for c in self.lines[i + 1 : j]], i, j))
            i = j
        return out

    def find(self, pattern: str) -> list[Section]:
        """Sections whose header matches a regex (anchored at the start)."""
        rx = re.compile(pattern)
        return [s for s in self.sections() if rx.match(s.header)]

    def section(self, header: str) -> Section | None:
        for s in self.sections():
            if s.header == header:
                return s
        return None

    def interfaces(self) -> dict[str, list[str]]:
        return {s.header.split(None, 1)[1]: s.children for s in self.find(r"interface \S")}

    def interface(self, name: str) -> list[str] | None:
        return self.interfaces().get(canonical_ifname(name))

    def replace_section(self, header: str, children: Iterable[str]) -> None:
        """Rewrite one section's children in place (header must exist)."""
        s = self.section(header)
        if s is None:
            raise LabModelError(f"section not found: {header!r}")
        self.lines[s.start + 1 : s.end] = [c if c.startswith(" ") else " " + c for c in children]

    def global_lines(self) -> list[str]:
        return [s.header for s in self.sections() if not s.children]

    def features(self) -> set[str]:
        """Coarse feature tags used by fault ``applies_when`` rules."""
        t = self.text
        tags = set()
        checks = {
            "ospf": r"^router ospf ",
            "eigrp": r"^router eigrp ",
            "hsrp": r"^ standby \d+ ip ",
            "etherchannel": r"^ channel-group \d+ mode ",
            "lacp": r"^ channel-protocol lacp",
            "pagp": r"^ channel-protocol pagp",
            "trunk": r"^ switchport mode trunk",
            "access": r"^ switchport mode access",
            "port_security": r"^ switchport port-security$",
            "svi": r"^interface Vlan\d+",
            "dhcp_server": r"^ip dhcp pool ",
            "dhcp_relay": r"^ ip helper-address ",
            "nat": r"^ip nat ",
            "acl": r"^(ip access-list |access-list )",
            "static_route": r"^ip route ",
            "mst": r"^spanning-tree mode mst",
            "rapid_pvst": r"^spanning-tree mode rapid-pvst",
            "stp_priority": r"^spanning-tree (vlan \S+|mst \S+) priority ",
            "root_guard": r"^ spanning-tree guard root",
            "bpdu_guard": r"^ spanning-tree bpduguard enable",
            "routed_port": r"^ no switchport",
        }
        for tag, rx in checks.items():
            if re.search(rx, t, re.M):
                tags.add(tag)
        if re.search(r"^no ip routing", t, re.M):
            tags.add("no_ip_routing")
        return tags

    def sticky_macs(self) -> dict[str, list[str]]:
        """Interfaces with saved sticky port-security MACs: {iface: [mac, ...]}."""
        out = {}
        for name, children in self.interfaces().items():
            macs = [
                c.split()[-1]
                for c in children
                if re.match(r"^ switchport port-security mac-address sticky [0-9a-f.]+$", c)
            ]
            if macs:
                out[name] = macs
        return out


# --------------------------------------------------------------------------- sanitizer

BANNER_RE = re.compile(r"^banner (exec|incoming|login|motd|prompt-timeout|slip-ppp)\s+(\S+)")
EULA_RE = re.compile(r"(END USER LICENSE|EULA|cisco\.com/go/eula)", re.I)


def sanitize_issues(text: str) -> list[str]:
    """Problems that must not be published (banner blocks, EULA text)."""
    issues = []
    for i, line in enumerate(text.split("\n"), 1):
        if BANNER_RE.match(line):
            issues.append(f"line {i}: {line.strip()[:40]}")
        elif EULA_RE.search(line):
            issues.append(f"line {i}: EULA text")
    return issues


def strip_banners(text: str) -> str:
    """Remove banner blocks from IOS config text (exports show the delimiter as ^C)."""
    lines = text.split("\n")
    out: list[str] = []
    i = 0
    while i < len(lines):
        m = BANNER_RE.match(lines[i])
        if not m:
            out.append(lines[i])
            i += 1
            continue
        token = m.group(2)
        delim = "^C" if token.startswith("^C") else token[0]
        rest = lines[i][m.start(2) + len(delim) :]
        i += 1
        if delim in rest:  # single-line banner
            continue
        while i < len(lines) and delim not in lines[i]:
            i += 1
        i += 1  # skip the closing delimiter line
    return "\n".join(out)


# --------------------------------------------------------------------------- topology


@dataclass
class Endpoint:
    node: str  # node label
    interface: str  # interface label

    def __str__(self) -> str:
        return f"{self.node} {self.interface}"


@dataclass
class Link:
    id: str
    a: Endpoint
    b: Endpoint

    def other(self, node: str) -> Endpoint:
        return self.b if self.a.node == node else self.a

    def has(self, node: str) -> bool:
        return node in (self.a.node, self.b.node)


@dataclass
class Node:
    raw: dict[str, Any] = field(repr=False)

    @property
    def id(self) -> str:
        return self.raw["id"]

    @property
    def label(self) -> str:
        return self.raw["label"]

    @property
    def node_definition(self) -> str:
        return self.raw["node_definition"]

    @property
    def interfaces(self) -> dict[str, str]:
        return {i["id"]: i["label"] for i in self.raw.get("interfaces") or []}

    @property
    def is_ios(self) -> bool:
        return self.node_definition in IOS_DEFINITIONS

    @property
    def is_wlc(self) -> bool:
        return self.node_definition in WLC_DEFINITIONS

    @property
    def is_l2_switch(self) -> bool:
        return self.node_definition in L2_SWITCH_DEFINITIONS

    @property
    def is_host(self) -> bool:
        return self.node_definition in HOST_DEFINITIONS

    @property
    def is_infra(self) -> bool:
        return self.node_definition in INFRA_DEFINITIONS

    @property
    def counts_against_licence(self) -> bool:
        return not self.is_infra

    @property
    def config_name(self) -> str | None:
        files = [f["name"] for f in self.raw.get("configuration") or []]
        want = CONFIG_FILE_BY_DEFINITION.get(self.node_definition)
        if want in files:
            return want
        if self.is_ios or self.is_wlc:
            raise LabModelError(
                f"{self.label} ({self.node_definition}) has no {want}; files: {files or 'none'}"
            )
        return None

    def config_file(self, name: str) -> str | None:
        for f in self.raw.get("configuration") or []:
            if f["name"] == name:
                return f.get("content") or ""
        return None

    def set_config_file(self, name: str, content: str) -> None:
        for f in self.raw.get("configuration") or []:
            if f["name"] == name:
                f["content"] = content
                return
        raise LabModelError(f"{self.label} has no config file {name}")

    @property
    def config(self) -> IosConfig | None:
        """Parsed config for IOS-family nodes and the WLC, else None."""
        if not (self.is_ios or self.is_wlc):
            return None
        return IosConfig(self.config_file(self.config_name) or "")

    def save_config(self, cfg: IosConfig) -> None:
        self.set_config_file(self.config_name, cfg.text)


class LabModel:
    """One lab: its topology export plus the repo folder it came from."""

    def __init__(self, data: dict[str, Any], source: Path | None = None) -> None:
        self.data = data
        self.source = source
        version = str(data.get("lab", {}).get("version", ""))
        if not version.startswith("0.3"):
            raise LabModelError(f"unsupported CML schema {version!r} (expected 0.3.x)")
        self.nodes = [Node(n) for n in data["nodes"]]
        self._by_label = {n.label: n for n in self.nodes}
        if len(self._by_label) != len(self.nodes):
            raise LabModelError("duplicate node labels in topology")
        self._by_id = {n.id: n for n in self.nodes}
        self.links = [self._resolve(link) for link in data.get("links") or []]

    # ---- loading / writing

    @classmethod
    def load(cls, path: Path | str) -> "LabModel":
        path = Path(path)
        with path.open(encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh), source=path)

    @property
    def title(self) -> str:
        return self.data["lab"]["title"]

    @property
    def folder(self) -> str | None:
        return self.source.parent.name if self.source else None

    def to_yaml(self, title: str | None = None) -> str:
        """Importable CML YAML; ``title`` overrides lab.title in the output only."""
        data = self.data
        if title is not None:
            data = dict(self.data)
            data["lab"] = dict(self.data["lab"], title=title)
        return yaml.dump(data, Dumper=_CmlDumper, sort_keys=False, allow_unicode=True, width=4096)

    # ---- lookups

    def node(self, label: str) -> Node:
        try:
            return self._by_label[label]
        except KeyError:
            raise LabModelError(f"no node labelled {label!r} in {self.title}") from None

    def has_node(self, label: str) -> bool:
        return label in self._by_label

    def links_of(self, label: str) -> list[Link]:
        return [link for link in self.links if link.has(label)]

    def peer(self, label: str, interface: str) -> Endpoint | None:
        """What is cabled to node ``label`` interface ``interface``."""
        interface = canonical_ifname(interface)
        for link in self.links:
            for here, there in ((link.a, link.b), (link.b, link.a)):
                if here.node == label and canonical_ifname(here.interface) == interface:
                    return there
        return None

    def neighbors(self, label: str) -> list[str]:
        return sorted({link.other(label).node for link in self.links_of(label)})

    def ios_nodes(self) -> list[Node]:
        return [n for n in self.nodes if n.is_ios]

    def switches(self) -> list[Node]:
        return [n for n in self.nodes if n.is_l2_switch]

    def licensed_node_count(self) -> int:
        return sum(1 for n in self.nodes if n.counts_against_licence)

    def sanitize_issues(self) -> dict[str, list[str]]:
        out = {}
        for n in self.nodes:
            for f in n.raw.get("configuration") or []:
                issues = sanitize_issues(f.get("content") or "")
                if issues:
                    out[f"{n.label}/{f['name']}"] = issues
        return out

    def _resolve(self, raw: dict[str, Any]) -> Link:
        def end(node_id: str, iface_id: str) -> Endpoint:
            node = self._by_id.get(node_id)
            if node is None:
                raise LabModelError(f"link {raw.get('id')} references unknown node {node_id}")
            label = node.interfaces.get(iface_id)
            if label is None:
                raise LabModelError(f"link {raw.get('id')}: {node.label} has no interface {iface_id}")
            return Endpoint(node.label, label)

        return Link(raw["id"], end(raw["n1"], raw["i1"]), end(raw["n2"], raw["i2"]))


class _CmlDumper(yaml.SafeDumper):
    """Write multi-line strings (configs) as literal blocks, like CML exports."""

    def increase_indent(self, flow: bool = False, indentless: bool = False):
        # CML exports indent list items under their key ("nodes:\n  - id: n0").
        return super().increase_indent(flow, False)


def _str_representer(dumper: yaml.SafeDumper, value: str):
    style = "|" if "\n" in value else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_CmlDumper.add_representer(str, _str_representer)


# --------------------------------------------------------------------------- labs and profiles


def lab_dirs() -> list[Path]:
    return sorted(p for p in REPO_ROOT.iterdir() if p.is_dir() and LAB_DIR_RE.match(p.name))


def resolve_lab(name: str) -> Path:
    """'03', '03-switching-redundancy' or a unique prefix -> lab folder."""
    matches = [p for p in lab_dirs() if p.name == name or p.name.startswith(name)]
    if len(matches) != 1:
        options = ", ".join(p.name for p in lab_dirs())
        raise LabModelError(f"lab {name!r} matches {len(matches)} folders; choose one of: {options}")
    return matches[0]


@dataclass
class LabProfile:
    """labs/<lab>.yaml: what the export can't carry (VLANs, intent, baseline)."""

    raw: dict[str, Any]
    path: Path

    @classmethod
    def load(cls, lab: str) -> "LabProfile":
        path = PROFILES_DIR / f"{resolve_lab(lab).name}.yaml"
        if not path.exists():
            raise LabModelError(f"no profile yet for {resolve_lab(lab).name} ({path.name})")
        with path.open(encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh) or {}, path)

    @property
    def lab(self) -> str:
        return self.raw["lab"]

    @property
    def source(self) -> Path:
        return (self.path.parent / self.raw["source"]).resolve()

    def model(self) -> LabModel:
        model = LabModel.load(self.source)
        self.validate(model)
        return model

    @property
    def boot(self) -> dict[str, Any]:
        return {"timeout_min": 20, "ready_check": "show version", **(self.raw.get("boot") or {})}

    @property
    def rebuild(self) -> dict[str, Any]:
        return self.raw.get("rebuild") or {}

    @property
    def vlans(self) -> dict[int, str | None]:
        return {int(k): v for k, v in (self.rebuild.get("vlans") or {}).items()}

    @property
    def never_touch(self) -> set[str]:
        return set(self.raw.get("never_touch") or [])

    def validate(self, model: LabModel) -> None:
        """Every node a profile names must exist in the topology."""
        named: list[str] = list(self.never_touch)
        rb = self.rebuild
        vtp = rb.get("vtp") or {}
        if vtp.get("primary_server"):
            named.append(vtp["primary_server"])
        leases = rb.get("renew_leases") or {}
        named += list(leases.get("hosts") or [])
        if leases.get("dhcp_server"):
            named.append(leases["dhcp_server"])
        intent = self.raw.get("intent") or {}
        for key in ("stp_root", "hsrp_active"):
            named += list((intent.get(key) or {}).values())
        for key in ("ospf_neighbors", "etherchannel"):
            named += list((intent.get(key) or {}).keys())
        missing = sorted({n for n in named if not model.has_node(n)})
        if missing:
            raise LabModelError(f"{self.path.name} names nodes not in the topology: {missing}")
        if vtp and not model.node(vtp["primary_server"]).is_l2_switch:
            raise LabModelError("vtp.primary_server must be a switch")
