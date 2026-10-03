# Networking Labs

Hands-on networking labs built in Cisco Modeling Labs (CML). Each lab includes the
topology export, the device configurations, and a writeup covering the design, the
verification steps, and the problems I hit along the way.

CCNA certified (Sept 2026), CCNP ENCOR in progress.

**Actively maintained:** new labs and updates are added every weekend.

> [!WARNING]
> ## Importing a switching lab? Re-create the VLANs first
>
> **CML exports do not include the VLAN database.** IOSvL2 switches store their VLANs
> **and** their VTP settings in `vlan.dat` in flash, not in the running-config, so they are
> not in any `topology.yaml` in this repo.
>
> After an import, every port and trunk still references its VLANs, but the VLANs
> themselves no longer exist:
>
> - access ports go **inactive**
> - trunks have **no active VLANs** to carry
> - SVIs stay **down**, so hosts **cannot reach their default gateway**
>
> **Before testing any switching lab:** re-create the VLANs listed in that lab's README (and
> VTP, where the lab uses it), confirm with `show vlan brief`, then renew the hosts' DHCP leases.
>
> Using VTP does not avoid this step, because VTP's own domain, version and roles live in the
> same `vlan.dat`. [Lab 03](03-switching-redundancy/) walks through the full rebuild with
> VTP version 3 and the troubleshooting that found the cause.

## Labs

| # | Lab | Focus | Key technologies |
|---|-----|-------|------------------|
| 01 | [Switching Fundamentals](01-switching-fundamentals/) | Starter lab: my first CML build, a dual-homed L2 access layer with loop-free redundancy | VLANs, 802.1Q trunking, native-VLAN hardening, PVST root load-balancing, PortFast/BPDU Guard |
| 02 | [Multi-Area OSPF Campus](02-multi-area-ospf-campus/) | Two-site enterprise network with a redundant NAT edge and centralized DHCP | Multi-area OSPF, multilayer SVIs, dual NAT/PAT, DHCP relay — verified end-to-end with capture output |
| 03 | [Redundant Campus Access Layer](03-switching-redundancy/) | Three-tier campus that survives any single link or switch failure | Rapid-PVST + root election, EtherChannel (LACP/PAgP/static), HSRP load-sharing, port-security, VTP v3, OSPF underlay + DHCP relay — verified with decoded packet captures |
| 04 | [Enterprise Campus Wireless](04-enterprise-wireless/) | Campus wireless with a Catalyst 9800-CL controller over a dual-homed, HSRP-routed core | Catalyst 9800-CL tag-based WLAN (WLAN/policy/site/RF tags), WPA2-PSK, dual LACP EtherChannel, HSRP load-sharing, `hostapd`/`wpa_supplicant` 802.11 — verified with capture output |
| 05 | [MST ↔ PVST+ Interoperability](05-mst-pvst-interoperability/) | Three-switch MST region bordered by a Rapid PVST+ ring, and how each side sees the other | MST (802.1s) region and instance mapping, root placement enforced with root guard, PVST simulation at the boundary, long path-cost alignment, trunk and edge-port hardening — verified with `show` output and decoded BPDU captures |
| 06 | [EIGRP Classic ↔ Named Interoperability](06-eigrp-classic-named-interoperability/) | One EIGRP AS built in both configuration styles, authenticated on every adjacency, with deliberate path selection to a test prefix | EIGRP classic + named mode interoperability, MD5 and HMAC-SHA-256 neighbor authentication, offset-list and per-neighbor AD path control, classic vs wide metrics, centralized DHCP relay — verified with `show`/`debug` output and decoded EIGRP captures |

Each lab folder has its own README with the full topology diagram, addressing tables,
a config walkthrough, and the verification steps.

## Why this repo exists

Configuring a protocol once from a tutorial doesn't teach much. Breaking it and
figuring out why it broke does. Where a lab fought me while I built it, I write down
what actually went wrong and how I isolated it. That's the part I find most useful to
record, and the part I'd want to talk through in an interview.

## Repo structure

Each lab folder follows the same layout:

```
0X-lab-name/
├── README.md          # Objective, topology, addressing, design, verification
├── topology.yaml      # CML topology export — import directly into CML
├── topology.svg       # Hand-built topology diagram
├── cml-canvas.png     # Screenshot of the running lab in CML
└── captures/          # (some labs) packet captures referenced in the README
```

To run a lab yourself: download its `topology.yaml`, then in CML choose
**Import** and select the file. Device configs are embedded in the export.

## Environment

- **Cisco Modeling Labs** (personal edition)
- Node types: IOSv, IOSvL2, IOSv-L3, IOL-XE, Catalyst 9800-CL (IOS-XE), Ubuntu (hostapd/wpa_supplicant)

## Contact

Roman Schroeder, Kyle, TX
[linkedin.com/in/roman-schroeder101](https://www.linkedin.com/in/roman-schroeder101/) · schroedermroman@gmail.com

## License & disclaimer

The lab topologies, configurations, and documentation in this repository are my own work, released under the MIT License.

Cisco, IOS, IOS XE, and Cisco Modeling Labs are trademarks of Cisco Systems, Inc. This repository is not affiliated with or endorsed by Cisco. It contains no Cisco software, images, or license material. Importing and running these labs requires your own licensed Cisco Modeling Labs instance with the referenced node images installed.
