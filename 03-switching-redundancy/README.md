# Redundant Campus Access Layer — Dual-Homed L2/L3 Campus with HSRP, EtherChannel & Rapid-PVST

![Platform](https://img.shields.io/badge/Platform-Cisco%20Modeling%20Labs-1BA0D7?logo=cisco&logoColor=white)
![Switches](https://img.shields.io/badge/Switches-IOSv--L2%20(15.2)-005073)
![Router](https://img.shields.io/badge/Router-IOSv%20(15.9)-0b7285)
![STP](https://img.shields.io/badge/L2-Rapid--PVST%20%2B%20Root%20Election-047857)
![EtherChannel](https://img.shields.io/badge/Aggregation-EtherChannel%20LACP%20%2F%20PAgP%20%2F%20static-1d4ed8)
![FHRP](https://img.shields.io/badge/Gateway-HSRP%20Load--Sharing-b91c1c)
![Port-Security](https://img.shields.io/badge/Access-Port--Security%20%2B%20PortFast%2FBPDU--Guard-b45309)
![OSPF](https://img.shields.io/badge/Underlay-OSPF%20%2B%20DHCP%20Relay-0d9488)
![VTP](https://img.shields.io/badge/VLANs-VTP%20v3%20(primary%20server)-6d28d9)

## Objective

This lab builds a **redundant, dual-homed campus** and proves it stays up when a link or a switch fails. Three access switches each home to **both** distribution switches; the distribution pair aggregates to a **redundant L3 core** where every VLAN's default gateway is an **HSRP** virtual IP, and every inter-switch connection is an **EtherChannel**. The design goal is that a host keeps its gateway, its uplink, and its path to the DHCP server through any single failure. It proves four Layer-2/redundancy competencies working together — Rapid-PVST with a deterministic, load-shared root; EtherChannel in all three negotiation modes; HSRP active/standby load-sharing; and access-port security — over an OSPF underlay with centralized DHCP. The VLAN database itself is distributed from Core1 with **VTP version 3**.

> [!WARNING]
> ## Before you run this lab: rebuild VTP and the VLANs
>
> **The VLANs and VTP are not in `topology.yaml`.** IOSvL2 keeps its VLAN database *and* its
> VTP settings in `vlan.dat`, not in the running-config, so the CML export cannot carry them.
> After a fresh import every switch boots with only the default VLANs. The access ports and
> trunks still reference VLANs 10, 20, 30 and 99, but those VLANs do not exist, so **hosts
> cannot reach their gateway SVIs** until they are re-created.
>
> Moving to VTP v3 does not remove this step: VTP's domain, version and roles live in the same
> `vlan.dat`. Rebuild in this order (full steps in *How to Run & Verify*):
>
> 1. **Every switch:** `vtp domain ROMANVTP` and `vtp version 3`
> 2. **Core1:** `vtp mode server`, then `vtp primary vlan` (privileged EXEC), then create VLANs **10, 20, 30, 99**
> 3. **Core2, Dist1, Dist2, A1, A2, A3:** `vtp mode client`
> 4. **A1, A2, A3:** clear the saved sticky MACs (`clear port-security sticky`)
> 5. **Hosts:** renew DHCP leases, then check `show vlan brief` and `show vtp status`

### Changes from the previous export

This export is the lab re-imported from this repo, repaired, and re-exported. Cabling is unchanged (12 nodes, 22 links; CML renumbered the link IDs on import, so the link map below uses the new IDs). Core1, Core2, Dist1, Dist2 and R1 are line-for-line identical to the previous export; the only configuration differences are on the access switches.

| Device(s) | What changed | Why it matters |
|-----------|--------------|----------------|
| A1, A2, A3 | Each host port now carries a saved sticky MAC (`switchport port-security mac-address sticky 5254.xxxx.xxxx`) | The addresses learned in this run were written to the config. On a new import CML normally gives the desktops new MACs, so clear these first (see *How to Run & Verify*) |
| A3 | Added `no ip routing` and `no ip cef` | A3 is now a pure Layer-2 switch like Dist1/Dist2; A1/A2 still have IOSvL2's default routing enabled (harmless, no SVIs) |
| All switches | VTP version 3, domain `ROMANVTP`, Core1 primary server, all others clients; VLANs 10, 20, 30, 99 re-created | Built on the running lab after troubleshooting the missing VLANs. **Not visible in the export** (lives in `vlan.dat`); the domain name is confirmed on the wire by CDP in the new captures |
| (new) `captures/` | Three packet captures from the repaired lab | Decoded below: HSRP, PAgP, Rapid-PVST, OSPF and CDP on the Core1 uplinks, plus host pings reaching R1 |

## Topology

<p align="center">
  <img src="topology.svg" alt="Redundant campus access-layer topology" width="960">
</p>

*Line key: teal double line = EtherChannel bundle (2 physical links), labelled with its Port-channel and negotiation mode (Po1 LACP · Po2 PAgP · Po3 static "on") · green = 802.1Q trunk carrying the access-switch dual-homing (STP forwards one, blocks the other per VLAN) · navy = routed OSPF point-to-point /30 to the edge router · gray = access port to a host. Each non-bundled endpoint shows its interface; the cores carry STP-root and HSRP-active badges per VLAN, and Core1 a VTP v3 primary-server badge; host VLAN membership is shown as a coloured chip. Purple `pcap` tags mark the links captured in `captures/`.*

## Node Inventory

| Node | Role | Type | `node_definition` |
|------|------|------|-------------------|
| Core1 | L3 core — SVIs, HSRP active VLAN 10/30, STP root VLAN 10/30, VTP v3 primary server (RID 2.2.2.2) | Multilayer switch | `iosvl2` |
| Core2 | L3 core — SVIs, HSRP active VLAN 20, STP root VLAN 20, VTP client (RID 3.3.3.3) | Multilayer switch | `iosvl2` |
| Dist1 | L2 distribution / aggregation (`no ip routing`), VTP client | Switch | `iosvl2` |
| Dist2 | L2 distribution / aggregation (`no ip routing`), VTP client | Switch | `iosvl2` |
| A1 | Access switch — VLAN 10 (Employees), VTP client | Switch | `iosvl2` |
| A2 | Access switch — VLAN 20 (Management), VTP client | Switch | `iosvl2` |
| A3 | Access switch — VLAN 30 (Service) + VLAN 10 (Employees) (`no ip routing`), VTP client | Switch | `iosvl2` |
| R1 | Edge router — central DHCP server + OSPF (RID 1.1.1.1) | Router | `iosv` |
| E-1 | End host, VLAN 10 (Employees) — on A1 | Desktop | `desktop` |
| M-1 | End host, VLAN 20 (Management) — on A2 | Desktop | `desktop` |
| S-1 | End host, VLAN 30 (Service) — on A3 | Desktop | `desktop` |
| E-2 | End host, VLAN 10 (Employees) — on A3 | Desktop | `desktop` |

The two **Core** switches hold all Layer-3 (SVIs, HSRP, OSPF) and are the STP roots; the two **Dist** switches are pure Layer-2 aggregation (`no ip routing`), and A3 now runs the same way. This is a routed-core model: the L3 boundary sits at the core, and Distribution is an L2 aggregation tier between access and core.

## Addressing & Segmentation

All values are read from the running-configs.

### VLANs

| VLAN | Name | Subnet | Gateway (HSRP VIP) | Host ports |
|------|------|--------|--------------------|------------|
| 10 | Employees | `10.1.10.0/24` | `10.1.10.3` | A1 Gi0/0 → E-1; A3 Gi0/3 → E-2 |
| 20 | Management | `10.1.20.0/24` | `10.1.20.3` | A2 Gi0/0 → M-1 |
| 30 | Service | `10.1.30.0/24` | `10.1.30.3` | A3 Gi0/0 → S-1 |
| 99 | native / unused | — | — | native VLAN on every trunk |

*The CML export carries no `vlan` stanzas at all: the VLAN database lives in `vlan.dat`, so neither the VLANs nor their names are in the file (see the import note above). The names above are from the lab's topology annotations, and `show vlan brief` on the switches confirms them. VLAN 99 is the unused native VLAN on all trunks (a VLAN-hopping hardening step); it carries no SVI and no host. VLAN 10 spans two access switches (A1 and A3), so it can be used to test intra-VLAN reachability across the switched core.*

### VLAN database distribution (VTP version 3)

| Setting | Value | Source |
|---------|-------|--------|
| Domain | `ROMANVTP` | CDP VTP-domain TLV from Core1, Dist1 in `captures/` |
| Version | 3 | Author's build notes (not in the export) |
| Primary server | Core1 (`vtp primary vlan`) | Author's build notes |
| Clients | Core2, Dist1, Dist2, A1, A2, A3 | Author's build notes |
| VLANs defined | 10, 20, 30, 99 | Referenced by every trunk and access port in the config |

*None of these settings appear in `topology.yaml`; VTP state is stored in `vlan.dat` with the VLANs. A VTP password, if one was set, is not recorded here either.*

### First-hop redundancy (HSRP)

Each VLAN has one virtual gateway (`.3`) shared by both cores; the active role is split per VLAN so both cores forward in steady state.

| VLAN | Group | VIP | Core1 (`.1`) | Core2 (`.2`) | Active |
|------|-------|-----|--------------|--------------|--------|
| 10 | 0 | `10.1.10.3` | priority 255, preempt | priority 1 | **Core1** |
| 20 | 1 | `10.1.20.3` | priority 1 | priority 255, preempt | **Core2** |
| 30 | 2 | `10.1.30.3` | priority 255, preempt | priority 1 | **Core1** |

### Spanning tree (Rapid-PVST)

Root priorities live on the cores and mirror the HSRP active role, so the L2 path leads to the live gateway. Distribution and access run default priority.

| VLAN | Root (priority 4096) | Secondary (priority 8192) |
|------|----------------------|----------------------------|
| 10, 30 | **Core1** | Core2 |
| 20 | **Core2** | Core1 |
| 99 | not set (default 32768) | elected by lowest bridge MAC |

VLAN 99 was never given a priority, so its root depends on MAC addresses that CML assigns at import. In the original build it rooted on Dist1; in the re-imported lab the captured BPDUs show Core1 designated toward Dist1 at root cost 3, which by elimination places the VLAN 99 root on Dist2. See *Possible Extensions*.

### OSPF (process 1, area 0) and the routed edge

| Router | Router-ID | Interfaces (→ area 0) | Role |
|--------|-----------|------------------------|------|
| R1 | 1.1.1.1 | Gi0/1 `192.168.1.1`, Gi0/0 `192.168.1.5` | Central DHCP server; OSPF only |
| Core1 | 2.2.2.2 | Gi1/0 `192.168.1.2`; SVIs `10.1.10/20/30.1` | L3 core |
| Core2 | 3.3.3.3 | Gi1/0 `192.168.1.6`; SVIs `10.1.10/20/30.2` | L3 core |

| Subnet | Endpoints |
|--------|-----------|
| `192.168.1.0/30` | Core1 Gi1/0 `.2` ↔ R1 Gi0/1 `.1` (routed, `ip ospf network point-to-point`) |
| `192.168.1.4/30` | Core2 Gi1/0 `.6` ↔ R1 Gi0/0 `.5` (routed, point-to-point) |

Inter-VLAN routing happens on the cores' SVIs. R1 is reached only over the two /30s; it runs no other interface and originates no default route, so the lab is self-contained (no Internet egress) by design.

### DHCP (server = R1)

| Pool | Network | default-router | Excluded |
|------|---------|----------------|----------|
| `VLAN10` | `10.1.10.0/24` | `10.1.10.3` | `.1`–`.10` |
| `VLAN20` | `10.1.20.0/24` | `10.1.20.3` | `.1`–`.10` |
| `VLAN30` | `10.1.30.0/24` | `10.1.30.3` | `.1`–`.10` |

Each pool hands out the **HSRP VIP** as the default gateway, so a client survives a core failover. The core SVIs relay to R1 with `ip helper-address`: Core1 → `192.168.1.1`, Core2 → `192.168.1.5` (each core's directly-connected R1 interface).

## Cabling / Link Map

Every link from the `links` list, interfaces resolved from each node's interface table. The ten core/distribution links form five 2-member EtherChannels; the six access uplinks are individual trunks. Link IDs are from the current export (CML renumbered them on re-import). **pcap** marks the three links captured in `captures/`.

| Link | A-side | B-side | Purpose |
|------|--------|--------|---------|
| l16 | Core1 Gi1/0 | R1 Gi0/1 | Routed OSPF p2p `192.168.1.0/30` · **pcap** |
| l15 | Core2 Gi1/0 | R1 Gi0/0 | Routed OSPF p2p `192.168.1.4/30` |
| l11 | Core1 Gi0/1 | Core2 Gi0/1 | **Po3** core-to-core (static `on`) member |
| l12 | Core1 Gi0/2 | Core2 Gi0/2 | **Po3** core-to-core (static `on`) member |
| l9 | Core1 Gi0/0 | Dist1 Gi0/3 | **Po2** Core1↔Dist1 (PAgP) member · **pcap** |
| l20 | Core1 Gi1/2 | Dist1 Gi1/2 | **Po2** Core1↔Dist1 (PAgP) member · **pcap** |
| l10 | Core2 Gi0/0 | Dist2 Gi0/3 | **Po2** Core2↔Dist2 (PAgP) member |
| l19 | Core2 Gi1/2 | Dist2 Gi1/2 | **Po2** Core2↔Dist2 (PAgP) member |
| l13 | Core1 Gi0/3 | Dist2 Gi1/0 | **Po1** Core1↔Dist2 (LACP) member |
| l18 | Core1 Gi1/1 | Dist2 Gi1/1 | **Po1** Core1↔Dist2 (LACP) member |
| l14 | Core2 Gi0/3 | Dist1 Gi1/0 | **Po1** Core2↔Dist1 (LACP) member |
| l17 | Core2 Gi1/1 | Dist1 Gi1/1 | **Po1** Core2↔Dist1 (LACP) member |
| l4 | Dist1 Gi0/0 | A1 Gi0/1 | Access uplink trunk (A1 → Dist1) |
| l7 | Dist2 Gi0/2 | A1 Gi0/2 | Access uplink trunk (A1 → Dist2) |
| l5 | Dist1 Gi0/1 | A2 Gi0/1 | Access uplink trunk (A2 → Dist1) |
| l6 | Dist2 Gi0/1 | A2 Gi0/2 | Access uplink trunk (A2 → Dist2) |
| l8 | Dist1 Gi0/2 | A3 Gi0/2 | Access uplink trunk (A3 → Dist1) |
| l3 | Dist2 Gi0/0 | A3 Gi0/1 | Access uplink trunk (A3 → Dist2) |
| l21 | A1 Gi0/0 | E-1 eth0 | Access port, VLAN 10 (port-security, PortFast/BPDU Guard) |
| l2 | A2 Gi0/0 | M-1 eth0 | Access port, VLAN 20 (port-security, PortFast/BPDU Guard) |
| l0 | A3 Gi0/0 | S-1 eth0 | Access port, VLAN 30 (port-security, PortFast/BPDU Guard) |
| l1 | A3 Gi0/3 | E-2 eth0 | Access port, VLAN 10 (port-security, PortFast/BPDU Guard) |

## Design Details

**Full dual-homing.** Every access switch has two uplinks, one to each distribution switch (A1: Gi0/1→Dist1, Gi0/2→Dist2; A2: Gi0/1→Dist1, Gi0/2→Dist2; A3: Gi0/2→Dist1, Gi0/1→Dist2). Each distribution switch in turn bundles to **both** cores, and the cores are joined to each other, so there is no single link or single box whose loss isolates an access switch. The redundant Layer-2 paths this creates are exactly what the spanning-tree and EtherChannel design below is there to manage.

**VLAN database distributed with VTP version 3.** Core1 is the VTP v3 *primary server* for the VLAN database, and every other switch is a client, so VLANs 10, 20, 30 and 99 are defined once on Core1 and learned everywhere else over the trunks. Version 3 was chosen over v1/v2 because only the primary server can change the database: a switch with a higher configuration revision that joins the domain cannot overwrite it, which removes the classic VTP failure where one stray switch wipes the campus VLANs. VTP rides the trunks on VLAN 1 even though VLAN 1 is pruned from every allowed list, because IOS keeps sending its VLAN 1 control traffic (VTP, CDP, PAgP) there; the captures show exactly that. These settings are stored in `vlan.dat`, so they are not in the export and must be rebuilt after an import.

```text
! Every switch
vtp domain ROMANVTP
vtp version 3
! Core1 (primary server)
vtp mode server
Core1# vtp primary vlan
vlan 10
 name Employees
vlan 20
 name Management
vlan 30
 name Service
vlan 99
! Core2, Dist1, Dist2, A1, A2, A3
vtp mode client
```

**Layer-3 core with per-VLAN SVIs and HSRP load-sharing.** Both cores own an SVI in every VLAN and share a virtual gateway per VLAN. Priorities split the active role so Core1 forwards VLAN 10/30 and Core2 forwards VLAN 20, with the other core preempt-ready as standby.

```text
! Core1
interface Vlan10
 ip address 10.1.10.1 255.255.255.0
 ip helper-address 192.168.1.1
 standby 0 ip 10.1.10.3
 standby 0 priority 255
 standby 0 preempt
interface Vlan20
 ip address 10.1.20.1 255.255.255.0
 standby 1 ip 10.1.20.3
 standby 1 priority 1
```

**Rapid-PVST with root election aligned to HSRP.** All switches run `rapid-pvst`. The root is pinned on the cores and matched to the HSRP active role per VLAN, so the forwarding tree leads straight to the live gateway instead of hair-pinning to a root on a different box. Roots are load-shared between the two cores.

```text
! Core1  (root + HSRP active for VLAN 10 and 30)
spanning-tree mode rapid-pvst
spanning-tree vlan 10,30 priority 4096
spanning-tree vlan 20    priority 8192
! Core2  (root + HSRP active for VLAN 20)
spanning-tree vlan 20    priority 4096
spanning-tree vlan 10,30 priority 8192
```

**EtherChannel across all three negotiation modes.** Every core/distribution connection is a two-link bundle, and the design deliberately exercises each way IOS forms a channel: **LACP** (open standard) on the core-to-far-distribution links, **PAgP** (Cisco) on the core-to-near-distribution links, and a static **`on`** channel between the two cores. Each bundle is an 802.1Q trunk, so a member link failure halves bandwidth without dropping the path or re-running STP.

```text
! Core1 Gi0/3 - LACP member of Po1 to Dist2
interface GigabitEthernet0/3
 channel-protocol lacp
 channel-group 1 mode active
! Core1 Gi0/0 - PAgP member of Po2 to Dist1
interface GigabitEthernet0/0
 channel-protocol pagp
 channel-group 2 mode desirable
! Core1 Gi0/1 - static member of Po3 to Core2
interface GigabitEthernet0/1
 channel-group 3 mode on
```

*The negotiated ends match: LACP `active`↔`passive`, PAgP `desirable`↔`auto`, static `on`↔`on`.*

**VLAN trunking and native-VLAN hardening.** All inter-switch links (physical trunks and Port-channels) are dot1q trunks pruned to `10,20,30,99`, with `switchport nonegotiate` and an unused **native VLAN 99** so untagged traffic never lands on VLAN 1.

```text
interface Port-channel1
 switchport trunk allowed vlan 10,20,30,99
 switchport trunk encapsulation dot1q
 switchport trunk native vlan 99
 switchport mode trunk
 switchport nonegotiate
```

**Access-port hardening.** Every host port is a single-VLAN access port with `port-security` (sticky MAC learning, `restrict` on violation), PortFast (fast host convergence), and BPDU Guard (err-disable if a switch is plugged in). The learned sticky MAC is now saved in each port's config (A1 shown below), which pins the port to the exact host seen in this run.

```text
interface GigabitEthernet0/0
 switchport access vlan 10
 switchport mode access
 switchport nonegotiate
 switchport port-security
 switchport port-security mac-address sticky
 switchport port-security mac-address sticky 5254.007c.8f24
 switchport port-security violation restrict
 spanning-tree portfast edge
 spanning-tree bpduguard enable
```

**OSPF underlay and centralized DHCP.** The two cores peer to the edge router `R1` over routed `/30` links (OSPF network type `point-to-point`), advertising the VLAN subnets into area 0. `R1` is the single DHCP server for all three VLANs; each core SVI relays to it, and every pool hands out the **HSRP VIP** as the default gateway so leases survive a core failover.

```text
! Core2 - routed uplink + OSPF
interface GigabitEthernet1/0
 no switchport
 ip address 192.168.1.6 255.255.255.252
 ip ospf network point-to-point
router ospf 1
 router-id 3.3.3.3
 network 10.1.10.0 0.0.0.255 area 0
 network 192.168.1.4 0.0.0.3 area 0
! R1 - DHCP with VIP gateway
ip dhcp pool VLAN10
 network 10.1.10.0 255.255.255.0
 default-router 10.1.10.3
```

## How to Run & Verify

**1. Import.** In CML, *Import* → select `topology.yaml` → start all nodes. Give the switches a minute to elect roots and form channels.

**2. Rebuild VTP and the VLANs.** The export does not carry `vlan.dat`, so every switch starts with only the default VLANs (see the import note at the top). Apply the VTP block from *Design Details* on every switch, run `vtp primary vlan` on Core1, then create VLANs 10, 20, 30 and 99 on Core1 and let VTP push them out. Without VTP, the same `vlan` commands can be entered on each switch individually; either way the VLANs must exist before anything else will pass traffic.

**3. Clear the saved sticky MACs.** The access ports carry the sticky MACs learned in the original run. If CML gave the desktops new MACs on import, A1 and A2 (default `maximum 1`) will drop the new host as a violation. Clear them and let the ports relearn:

```text
A1# clear port-security sticky interface GigabitEthernet0/0
A2# clear port-security sticky interface GigabitEthernet0/0
A3# clear port-security sticky interface GigabitEthernet0/0
A3# clear port-security sticky interface GigabitEthernet0/3
```

**4. Renew the hosts' DHCP leases** (or restart the desktops) so they lease from R1 now that the VLANs exist.

| Test (where) | Command | Expected result |
|--------------|---------|-----------------|
| VTP domain and roles | Core1 / any client: `show vtp status` | Version 3, domain `ROMANVTP`; Core1 `Server` and primary, others `Client` listing Core1 as primary |
| VLAN database propagated | A3: `show vlan brief` | VLANs 10, 20, 30, 99 present and active, with A3 Gi0/0 in 30 and Gi0/3 in 10 |
| SVIs up | Core1: `show ip interface brief \| include Vlan` | Vlan10/20/30 `up/up` (they stay `down` while the VLAN does not exist) |
| EtherChannels bundled | Core1: `show etherchannel summary` | Po1/Po2/Po3 in use (`SU`); Po1 protocol LACP, Po2 PAgP, Po3 `-` (static); both members `P` |
| STP root per VLAN | Core1: `show spanning-tree vlan 10` | Core1 is root for VLAN 10 (and 30); Core2 root for VLAN 20 |
| STP blocks a redundant uplink | A1: `show spanning-tree vlan 10` | one uplink `Root FWD`, the other `Altn BLK` |
| HSRP active/standby split | Core1 / Core2: `show standby brief` | Core1 Active VLAN 10/30, Core2 Active VLAN 20; VIP `.3`; peer Standby |
| Host gateway = VIP | E-1: `ip a` / default route | address in `10.1.10.0/24`, gateway `10.1.10.3` |
| DHCP bindings | R1: `show ip dhcp binding` | one binding per host across the three pools |
| Trunks / native VLAN | A3: `show interfaces trunk` | uplinks trunking `10,20,30,99`, native VLAN 99 |
| Port-security active | A1: `show port-security` | Gi0/0 secured, sticky MAC learned, violation `Restrict` |
| Inter-VLAN routing | E-1 (v10) → ping M-1 (v20) | success via the core SVIs |
| Intra-VLAN across the core | E-1 (A1) → ping E-2 (A3), both VLAN 10 | success across the switched core |
| OSPF adjacencies | Core1: `show ip ospf neighbor` | FULL with R1 and with Core2 |
| Gateway failover | ping the VIP from a host, then shut the active core's SVI/uplink | brief loss, then HSRP standby takes over; ping resumes |
| Uplink failover | shut one access uplink | STP moves the blocked uplink to forwarding; traffic continues |

### Troubleshooting: hosts could not reach their SVIs after re-import

**Symptom.** After importing this lab from the repo, the end hosts could not reach their gateways: pings to the HSRP VIP and to the core SVIs failed, even though the configuration looked identical to the working build.

**Isolation.** The configs, cabling and EtherChannels all checked out, so the fault had to be below the SVI. On IOS a VLAN interface only comes up when the VLAN exists in the database and has at least one forwarding port in it. The switches' VLAN databases held only the defaults: the access ports pointed at VLANs that did not exist, the trunks had no active VLANs from their allowed list to carry, and so the Core SVIs had nothing to come up on.

**Root cause.** IOSvL2 stores VLANs 1 to 1005 (and the VTP configuration) in `vlan.dat` in flash, not in the running-config. The CML export only captures the configuration text, so the `vlan` definitions never made it into `topology.yaml`, and the import rebuilt every switch without them.

**Fix.** VLANs 10, 20, 30 and 99 were re-created and VTP version 3 was put in place with Core1 as primary server, so the database is now defined in one place and learned by the rest of the switches. Because VTP's own settings also live in `vlan.dat`, a fresh import still needs steps 2 to 4 above; the difference is that the VLANs now only have to be typed once.

These commands show the fault on a freshly imported switch:

```text
show vlan brief                     ! only VLAN 1 and 1002-1005 listed
show interfaces trunk               ! "allowed and active in management domain" missing 10,20,30,99
show interfaces Gi0/0 switchport    ! "Access Mode VLAN: 10 (Inactive)"
show ip interface brief | i Vlan    ! core SVIs down
```

### Captured verification (CML)

The `show` output in this subsection is from the original build. The packet captures after it were taken on the repaired, re-imported lab (VTP v3) and agree with it.

The following was captured from the running lab.

**All three EtherChannel negotiation modes are bundled and forwarding.** Core1 runs an LACP bundle to Dist2, a PAgP bundle to Dist1, and a static channel to Core2; every member port shows `(P)` for bundled, and each channel is `SU` (in use, Layer 2). The blank protocol column on Po3 is the static `on` channel:

```text
Core1# show etherchannel summary
Number of channel-groups in use: 3
Number of aggregators:           3

Group  Port-channel  Protocol    Ports
------+-------------+-----------+-----------------------------------------------
1      Po1(SU)         LACP      Gi0/3(P)    Gi1/1(P)
2      Po2(SU)         PAgP      Gi0/0(P)    Gi1/2(P)
3      Po3(SU)          -        Gi0/1(P)    Gi0/2(P)
```

**Root bridges landed exactly where they were designed, load-shared across the cores.** Core1 is root for VLAN 10 and 30 (cost 0, no root port, because it *is* the root); VLAN 20 roots on Core2 and is reached over Po3. Priorities read as the configured value plus the VLAN ID, so `4096 + 10 = 4106`:

```text
Core1# show spanning-tree root

                                        Root    Hello Max Fwd
Vlan                   Root ID          Cost    Time  Age Dly  Root Port
---------------- -------------------- --------- ----- --- ---  ------------
VLAN0010          4106 5254.0011.0661         0    2   20  15
VLAN0020          4116 5254.009a.f611         3    2   20  15  Po3
VLAN0030          4126 5254.0011.0661         0    2   20  15
VLAN0099         32867 5254.0005.91ae         3    2   20  15  Po2
```

VLAN 99 is the visible exception and confirms a known gap: it was never given a priority, so it kept the default `32768 + 99 = 32867` and rooted by lowest MAC on a switch one EtherChannel hop away through Po2, rather than on a core. See *Possible Extensions*.

**HSRP active/standby is split between the cores.** Core1 is Active for VLAN 10 and 30, Core2 for VLAN 20, and each is the standby for the other, all sharing the `.3` virtual IP:

```text
Core1# show standby brief
Interface   Grp  Pri P State   Active          Standby         Virtual IP
Vl10        0    255 P Active  local           10.1.10.2       10.1.10.3
Vl20        1    1     Standby 10.1.20.2       local           10.1.20.3
Vl30        2    255 P Active  local           10.1.30.2       10.1.30.3

Core2# show standby brief
Interface   Grp  Pri P State   Active          Standby         Virtual IP
Vl10        0    1     Standby 10.1.10.1       local           10.1.10.3
Vl20        1    255 P Active  local           10.1.20.1       10.1.20.3
Vl30        2    1     Standby 10.1.30.1       local           10.1.30.3
```

**Port-security is live on the access edge.** A1's host port has learned its sticky MAC and is armed with the `Restrict` action, with no violations recorded:

```text
A1# show port-security
Secure Port  MaxSecureAddr  CurrentAddr  SecurityViolation  Security Action
                (Count)       (Count)          (Count)
---------------------------------------------------------------------------
      Gi0/0              1            1                  0         Restrict
```

**Gateway failover works, and the client never changes its configuration.** With a VLAN 10 host pinging the virtual IP, shutting Core1's `Vlan10` SVI moved the Active role to Core2 on the same `10.1.10.3` address:

```text
E-1:~$ ping 10.1.10.3
64 bytes from 10.1.10.3: seq=0 ttl=42 time=8.896 ms
...
--- 10.1.10.3 ping statistics ---
85 packets transmitted, 59 packets received, 30% packet loss

Core2# show standby brief
Interface   Grp  Pri P State   Active          Standby         Virtual IP
Vl10        0    1     Active  local           unknown         10.1.10.3
Vl20        1    255 P Active  local           10.1.20.1       10.1.20.3
Vl30        2    1     Standby 10.1.30.1       local           10.1.30.3
```

Core2 reports `Standby unknown` because Core1's SVI is down, and VLAN 30 stayed on Core1, confirming the test was scoped to a single VLAN. The recovery is real but slow: roughly 26 packets were lost, a longer outage than HSRP's default 10-second hold time implies. Tuning the hello/hold timers and adding interface tracking is the documented next step. See *Possible Extensions*.

#### Packet captures (decoded)

Three links on Core1 were captured on the repaired lab, just before the export (21:13 to 21:19 UTC). Together they cover every protocol this design depends on.

| File | Link | Duration | Frames | Contents |
|------|------|----------|--------|----------|
| `captures/core1-dist1_po2-gi1-2.pcap` | l20 Core1 Gi1/2 ↔ Dist1 Gi1/2 (Po2 member) | 323.5 s | 409 | 373 HSRP, 24 PAgP, 12 CDP |
| `captures/core1-dist1_po2-gi0-0.pcap` | l9 Core1 Gi0/0 ↔ Dist1 Gi0/3 (Po2 member) | 54.1 s | 213 | 109 Rapid-PVST BPDUs, 64 HSRP, 35 OSPF, 3 PAgP, 2 CDP |
| `captures/core1-r1_routed-p2p.pcap` | l16 Core1 Gi1/0 ↔ R1 Gi0/1 (routed /30) | 50.1 s | 225 | 202 ICMP, 10 OSPF, 11 Ethernet keepalives, 2 CDP |

**VTP domain on the wire.** Every CDP frame from Core1 and Dist1 carries the VTP management domain TLV `ROMANVTP`, and CDP reports native VLAN 99 on both ends. No VTP summary advertisement falls inside these windows (v3 sends one every five minutes, and on a bundle it leaves on only one member), so the domain name from CDP is the capture evidence for VTP.

```text
CDP  Dist1  GigabitEthernet1/2  VTP domain ROMANVTP  native VLAN 99   (802.1Q tag: VLAN 1)
CDP  Core1  GigabitEthernet1/2  VTP domain ROMANVTP  native VLAN 99   (802.1Q tag: VLAN 1)
```

**Control traffic stays on VLAN 1.** CDP and PAgP frames on the Po2 members are tagged **VLAN 1**, even though VLAN 1 is not in any trunk's allowed list. This is IOS behaviour when the native VLAN is moved off 1: control protocols keep using VLAN 1 and are tagged rather than sent native. It is also why VTP keeps working with VLAN 1 pruned.

**PAgP bundling (Po2).** Dist1 advertises *Auto* mode and Core1 *Desirable*, each lists the other's device ID as its partner, and both flag a consistent state with matching group capability `0x00020001`. That is the `desirable`↔`auto` pairing from the config, negotiated and stable.

**HSRP split, seen per VLAN.** All HSRP is version 1 (to `224.0.0.2`) with the default 3 s hello and 10 s hold timers. Active routers source hellos from the virtual MAC `0000.0c07.acXX` (XX = group), standby routers from their own MAC:

```text
VLAN 10  grp 0  Core1 10.1.10.1  pri 255  Active   src 0000.0c07.ac00  VIP 10.1.10.3
VLAN 30  grp 2  Core1 10.1.30.1  pri 255  Active   src 0000.0c07.ac02  VIP 10.1.30.3
VLAN 20  grp 1  Core1 10.1.20.1  pri 1    Standby  src 5254.0031.8014  VIP 10.1.20.3
VLAN 20  grp 1  Core2 10.1.20.2  pri 255  Active   src 0000.0c07.ac01  VIP 10.1.20.3
VLAN 10  grp 0  Core2 10.1.10.2  pri 1    Standby  src 5254.0046.800a  VIP 10.1.10.3
VLAN 30  grp 2  Core2 10.1.30.2  pri 1    Standby  src 5254.0046.801e  VIP 10.1.30.3
```

Core1's own hellos appear on Gi1/2 and Core2's hellos appear on Gi0/0: the two Po2 members each carry a different set of flows, consistent with per-source hashing across the bundle. Every hello also carries the default plaintext authentication string `cisco`, meaning no HSRP authentication is configured (see *Possible Extensions*).

**Rapid-PVST roots match the design.** Only Core1 sends BPDUs on Po2, and they leave on the Gi0/0 member (Dist1's end is a root or alternate port, which do not send BPDUs in RSTP). It announces itself as root for VLAN 10 and 30 and points at Core2 for VLAN 20:

```text
VLAN 10  root 4096+10  = Core1 (self)   cost 0   role Designated
VLAN 30  root 4096+30  = Core1 (self)   cost 0   role Designated
VLAN 20  root 4096+20  5254.0046.6922   cost 3   Core1 bridge 8192+20   role Designated
VLAN 99  root 32768+99 5254.0030.e073   cost 3   Core1 bridge 32768+99  role Designated   (untagged: native)
```

VLANs 10, 20 and 30 are sent tagged; VLAN 99's BPDU goes untagged because it is the native VLAN. VLAN 99's root is one bundle away from Core1 but is not Dist1 (Core1 is designated toward it) or Core2 (different bridge MAC), which leaves Dist2.

**OSPF runs on every SVI.** Core1 and Core2 exchange Hellos on VLANs 10, 20 and 30 (area 0, 10/40 s). With equal priority 1, Core2 (RID 3.3.3.3) is DR and Core1 BDR on each VLAN. Those Hellos are flooded to every host port in the user VLANs, which the design does not need (see *Possible Extensions*).

**Core1 ↔ R1: routed point-to-point and live host traffic.** The /30 carries OSPF Hellos with network type point-to-point (mask /30, no DR or BDR, 10/40 s) between 1.1.1.1 and 2.2.2.2. During the capture two hosts pinged R1 at `192.168.1.1`: `10.1.30.11` (VLAN 30) and `10.1.10.12` (VLAN 10). All 101 echo requests were answered. The requests arrive with TTL 63, one hop from the Linux default of 64, so each host's first and only hop was Core1: the HSRP active gateway for both VLANs. The addresses are above R1's `.1`–`.10` exclusion, consistent with DHCP leases from R1.

```text
10.1.30.11 → 192.168.1.1  echo request  TTL 63   51 sent / 51 replies
10.1.10.12 → 192.168.1.1  echo request  TTL 63   50 sent / 50 replies
OSPF Hello 2.2.2.2 ↔ 1.1.1.1  mask 255.255.255.252  hello 10  dead 40  DR 0.0.0.0
```

This is the repair verified end to end: host → access VLAN → trunk → Core1 SVI (HSRP active) → routed /30 → R1.

## Skills Demonstrated

- Redundant campus design: every access switch dual-homed to a distribution pair that aggregates to a redundant L3 core, with no single point of failure in the path.
- First-hop redundancy: HSRP with a per-VLAN virtual gateway and active/standby **load-sharing** across the two cores, and DHCP handing out the VIP so hosts actually benefit from it.
- Rapid-PVST with deliberate **root-bridge election**, load-shared across the cores and **aligned to the HSRP active role** per VLAN.
- EtherChannel in all three negotiation modes — **LACP**, **PAgP**, and static **`on`** — with matched partner modes, every bundle a trunk.
- VLAN design: 802.1Q trunking pruned per link, `nonegotiate`, and native-VLAN-99 hardening.
- Access-port hardening: port-security (sticky, restrict) with PortFast and BPDU Guard.
- OSPF underlay over routed point-to-point links and centralized DHCP with cross-subnet relay.
- VLAN database management with **VTP version 3**: a single primary server (Core1), clients everywhere else, and an understanding of why v3's primary-server model protects against revision-number overwrites.
- Troubleshooting from symptom to root cause: traced "hosts cannot reach the gateway" through SVI state to a missing VLAN database, and identified that `vlan.dat` (VLANs and VTP settings) is not part of a CML export.
- Packet-level analysis: decoded HSRP roles and virtual MACs, PAgP desirable/auto negotiation, per-VLAN Rapid-PVST BPDUs, OSPF DR/BDR and point-to-point Hellos, and CDP VTP-domain TLVs from CML captures, and used TTL to prove the host's first hop.
- Iterative build discipline: the topology and config were tightened over several revisions — fixing the DHCP gateway to the VIP, enabling port-security, moving to Rapid-PVST, and aligning the STP root to the HSRP active gateway.

## Possible Extensions

- **Tune the native VLAN's root.** VLAN 99 has no explicit priority, so its root follows MAC addresses that CML assigns at import: Dist1 in the original build, Dist2 after the re-import. Fold `99` into Core1's `priority 4096` line so the whole topology is deterministic across imports, even though nothing rides VLAN 99.
- **Make the VLANs survive an export.** In VTP transparent (or `off`) mode IOS writes the `vlan` stanzas into the running-config, so they would travel inside `topology.yaml`. That trades VTP's central management for portability; keeping v3 and documenting the rebuild (as here) is the other choice. If VTP stays, set a v3 password (`vtp password <secret> hidden`) so only authorised switches join the domain.
- **Keep the saved sticky MACs from breaking imports.** The access ports now carry this run's host MACs. Either clear them before exporting, or pin each desktop's `mac_address` in `topology.yaml` to the saved value so the ports match on every import.
- **Authenticate HSRP.** The captures show the default plaintext string `cisco` in every hello. Add `standby <grp> authentication md5 key-string <secret>` on both cores so a rogue device cannot claim the gateway.
- **Stop OSPF Hellos on the user VLANs.** The cores form adjacencies on all three SVIs, which floods Hellos to every host port. Make Vlan10/20/30 `passive-interface` (the cores still reach each other through R1) or keep a single transit SVI.
- **Match A1/A2 to A3.** A3 now runs `no ip routing`; A1 and A2 still have IOSvL2's default routing enabled. Apply the same line for a consistent L2 access tier.
- **Even out port-security limits.** A3 sets `maximum 2`; A1/A2 use the default of 1. Set an explicit `maximum` on every access port so the policy is uniform and intentional.
- **Cut HSRP failover time and add tracking.** The measured failover dropped roughly 26 packets, well beyond the 10-second default hold time, so a user would notice the outage. Lower the timers (`standby 0 timers 1 3`) and add interface or object tracking so the active core also steps down when it loses its path to R1, not only when the box itself fails. As configured, shutting a core's uplinks alone will not trigger failover, because it keeps its SVIs up and continues exchanging hellos over Po3.
- **Push the L3 boundary to distribution.** The gateways sit at the core today. Moving SVIs/HSRP (or a routed access model) down a tier would shrink the L2 domain — a natural "routed-access" follow-up.
- **Give R1 an exit.** R1 originates no default and has no upstream, so there is no Internet path. Add an `external_connector` and `default-information originate` for an egress story (and a link to the Security & Services lab).
- **Cosmetic:** the topology annotation labels the core-to-R1 links "PPP," but they are routed Ethernet `/30`s with OSPF network type `point-to-point`, not PPP encapsulation; and the `E-2` desktop still carries the default `inserthostname-here` node config.

## Files

| File | Description |
|------|-------------|
| `README.md` | This documentation. |
| `topology.svg` | Hand-built topology diagram (embedded above). |
| `topology.yaml` | Sanitized CML export (re-imported and repaired build) — Cisco banner/EULA blocks stripped from all eight IOS devices; all real configuration preserved (12 nodes, 22 links, parse-verified). VLANs and VTP are not in it; see the import note. |
| `captures/core1-dist1_po2-gi1-2.pcap` | HSRP, PAgP and CDP (VTP domain) on Po2 member l20, Core1 Gi1/2 ↔ Dist1 Gi1/2. |
| `captures/core1-dist1_po2-gi0-0.pcap` | Rapid-PVST BPDUs, HSRP, OSPF, PAgP and CDP on Po2 member l9, Core1 Gi0/0 ↔ Dist1 Gi0/3. |
| `captures/core1-r1_routed-p2p.pcap` | OSPF point-to-point Hellos, CDP and host pings to R1 on l16, Core1 Gi1/0 ↔ R1 Gi0/1. |

---

*Documentation derived from the device running-configs in the CML export `Switching` (re-imported build) and three CML packet captures. Cabling and interfaces come from the `links` list; VLAN assignments, HSRP, STP, EtherChannel, OSPF and DHCP come from each node's configuration; the VTP domain name comes from the captures, and the VTP version and roles from the author's build notes, since the export omits `vlan.dat` entirely.*
