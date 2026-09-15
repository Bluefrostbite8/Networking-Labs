# L2 Lab — Redundant Switched Campus Network

![Platform](https://img.shields.io/badge/Platform-Cisco%20Modeling%20Labs-1BA0D7)
![Node](https://img.shields.io/badge/Switch-IOSvL2%2015.2-005073)
![Layer](https://img.shields.io/badge/OSI-Layer%202-blueviolet)
![STP](https://img.shields.io/badge/Loop%20Prevention-PVST-orange)
![Root](https://img.shields.io/badge/STP%20Root-Per--VLAN%20Load%20Balanced-9b59b6)
![Security](https://img.shields.io/badge/Access%20Ports-PortFast%20%2B%20BPDU%20Guard-success)

A four-switch, two-host Layer 2 lab built in Cisco Modeling Labs (CML). Two access
switches dual-home into a distribution pair, deliberately creating physical loops
that Per-VLAN Spanning Tree (PVST) resolves. Root-bridge priorities are tuned so
the two VLANs load-balance across the distribution switches. The lab demonstrates
VLAN segmentation, 802.1Q trunking, native-VLAN hardening, deterministic STP root
selection, and edge-port protection.

---

## Objective

Design and verify a resilient switched access layer where:

- End hosts are segmented into VLANs and carried over tagged uplinks.
- Every access switch has **two independent paths** to the core, so a single link
  or distribution-switch failure does not isolate a host.
- The resulting L2 loops are managed by spanning tree rather than causing a
  broadcast storm.
- Edge ports facing hosts converge instantly and are protected against rogue
  switches.

---

## Topology

<p align="center">
  <img src="topology.svg" alt="L2 Lab topology: access switches S1/S2 dual-homed to distribution switches S3/S4, hosts M-D1/M-D2 on VLAN 10" width="820">
</p>

*Thick lines are 802.1Q trunks; thin lines are host-facing access ports. The
five inter-switch trunks form loops that PVST breaks by blocking redundant paths.*

---

## Node Inventory

| Node  | Role                | Type      | Node Definition |
|-------|---------------------|-----------|-----------------|
| S1    | Access switch       | Switch    | `iosvl2`        |
| S2    | Access switch       | Switch    | `iosvl2`        |
| S3    | Distribution/core   | Switch    | `iosvl2`        |
| S4    | Distribution/core   | Switch    | `iosvl2`        |
| M-D1  | End host            | Desktop   | `desktop`       |
| M-D2  | End host            | Desktop   | `desktop`       |

## VLAN Plan

| VLAN | Purpose               | Where used                                   |
|------|-----------------------|----------------------------------------------|
| 10   | Data (end hosts)      | Access ports to M-D1 / M-D2; allowed on trunks |
| 20   | Data (reserved)       | Allowed on all trunks; no hosts assigned yet |
| 99   | Native / unused       | Native VLAN on every trunk (not used for data) |

> Placing an unused VLAN (99) as the trunk native VLAN keeps user traffic off the
> native VLAN — a standard hardening step against VLAN-hopping.

## Cabling / Link Map

| Link | A-side          | B-side          | Mode           |
|------|-----------------|-----------------|----------------|
| L0   | S1 Gi0/0        | M-D1 eth0       | Access (VLAN 10) |
| L1   | S2 Gi0/0        | M-D2 eth0       | Access (VLAN 10) |
| L2   | S3 Gi0/0        | S1 Gi0/1        | Trunk          |
| L3   | S4 Gi0/0        | S2 Gi0/1        | Trunk          |
| L4   | S3 Gi0/1        | S2 Gi0/2        | Trunk          |
| L5   | S4 Gi0/1        | S1 Gi0/2        | Trunk          |
| L6   | S3 Gi0/2        | S4 Gi0/2        | Trunk          |

Each access switch (S1, S2) reaches **both** distribution switches (S3, S4), and
the distribution pair is directly interconnected — a near-full mesh that provides
path redundancy.

---

## Design Details

### VLAN segmentation
Hosts are placed into VLAN 10 at the access edge. VLANs are defined and named on
the switches; the CML config export omits the `vlan` definition stanzas, but the
access/trunk assignments below reference them.

### 802.1Q trunking
All inter-switch links are hardcoded trunks with encapsulation and VLAN pruning
made explicit rather than left to negotiation:

```text
interface GigabitEthernet0/1
 switchport trunk encapsulation dot1q
 switchport trunk allowed vlan 10,20
 switchport trunk native vlan 99
 switchport mode trunk
 switchport nonegotiate
```

- `switchport mode trunk` + `switchport nonegotiate` disable DTP, so trunks never
  form by accident.
- `allowed vlan 10,20` prunes every other VLAN off the uplinks.
- `native vlan 99` moves the untagged VLAN away from any data VLAN.

### Loop prevention (PVST)
With five trunks between four switches, the topology contains physical loops. Every
switch runs `spanning-tree mode pvst`, so a spanning tree is computed per VLAN and
redundant links are placed into a blocking state until needed.

### Deterministic root bridges with per-VLAN load balancing
Rather than leaving root election to the default lowest-MAC tiebreak, the two
distribution switches are given explicit priorities so each VLAN roots on a
different switch. This puts both distribution switches and their uplinks to work
instead of parking one in standby:

| Switch | VLAN 10 priority | VLAN 20 priority | Role                              |
|--------|------------------|------------------|-----------------------------------|
| **S4** | `24576` (root)   | `28672` (backup) | Root for VLAN 10, backup for VLAN 20 |
| **S3** | `28672` (backup) | `24576` (root)   | Root for VLAN 20, backup for VLAN 10 |

```text
! S4
spanning-tree vlan 10 priority 24576
spanning-tree vlan 20 priority 28672
! S3
spanning-tree vlan 10 priority 28672
spanning-tree vlan 20 priority 24576
```

The default bridge priority is `32768`; `24576` is equivalent to `root primary`
and `28672` to `root secondary`. VLAN 10 traffic therefore converges on **S4** and
VLAN 20 on **S3**, and each distribution switch is the ready backup for the other's
VLAN if a root fails.

### Edge-port protection
Host-facing access ports converge immediately and reject unexpected switches:

```text
interface GigabitEthernet0/0
 switchport mode access
 switchport access vlan 10
 spanning-tree portfast edge
 spanning-tree bpduguard enable
```

- **PortFast** skips the listening/learning delay for end devices.
- **BPDU Guard** err-disables the port if it ever receives a BPDU, preventing a
  rogue switch from joining or looping the network through a host port.

---

## How to Run

1. Open Cisco Modeling Labs.
2. **Import Lab** → select `L2_Lab.yaml`.
3. Start all nodes and open a console to each switch.
4. Suggested verification commands:

```text
show spanning-tree vlan 10        ! confirm S4 is root for VLAN 10
show spanning-tree vlan 20        ! confirm S3 is root for VLAN 20
show spanning-tree root           ! root bridge + root port per VLAN, at a glance
show interfaces trunk
show vlan brief
show interfaces status
```

On S4, `show spanning-tree vlan 10` should report *"This bridge is the root"*; on S3
the same holds for VLAN 20 — verifying the priorities took effect.

Ping between **M-D1** and **M-D2** to confirm intra-VLAN reachability across the
switched core, then shut a trunk (e.g. `interface Gi0/1 → shutdown` on S1) and
confirm connectivity survives via the redundant path.

---

## Skills Demonstrated

- VLAN design and access-layer segmentation
- 802.1Q trunk configuration with explicit allowed-VLAN pruning
- Native-VLAN hardening against VLAN hopping
- Spanning Tree (PVST) in a loop-redundant topology
- Deterministic root-bridge selection with per-VLAN load balancing across the distribution pair
- Edge-port security with PortFast + BPDU Guard
- Building and documenting labs in Cisco Modeling Labs

---

## Files

| File          | Description                                   |
|---------------|-----------------------------------------------|
| `L2_Lab.yaml` | CML topology export (nodes, links, configs)   |
| `topology.svg`| Topology diagram embedded above                |
| `README.md`   | This document                                 |
