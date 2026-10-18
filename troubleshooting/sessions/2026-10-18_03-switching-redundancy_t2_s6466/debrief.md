# Debrief 03-t2-s6466: Redundant Campus Access Layer

![Score](https://img.shields.io/badge/Score-100%2F100-047857)
![Grades](https://img.shields.io/badge/Grades-Exact-047857)
![Tier](https://img.shields.io/badge/Tier-T2%20Standard-b45309)
![Hints](https://img.shields.io/badge/Hints-0-6d28d9)
![Time](https://img.shields.io/badge/Time-8.5%20min-0b7285)

The debrief for [ticket](ticket.md) `03-t2-s6466` on lab 03 Redundant Campus Access Layer: 1 fault, seed 6466, graded 2026-10-18 16:00.

## Results

| # | Fault | Grade | Score | Hints | Time |
|---|-------|-------|-------|-------|------|
| 1 | Host port in the wrong access VLAN | Exact | 100 | none | 8.5 min |

Session score **100**: the average of the fault scores (100 × grade weight, minus hint penalties and 15 per collateral failure, never below 0). Time runs from `start` to `grade`; every fault is graded in the same pass.

## Fault 1: Host port in the wrong access VLAN

**Ticket:** E-2 is online, but it's sitting in the wrong department's network.

### What was broken

`l2-access-vlan` (l2, tier 1) was injected into the imported lab on A3, saved to startup so a reload would not undo it.

```diff
  ! A3, interface GigabitEthernet0/3
- switchport access vlan 10
+ switchport access vlan 30
```

### Your fix

**Exact**, score 100. Traffic works, the root cause is gone by state, the fix is saved, the design is intact, and the changed section matches the original line for line.

| Question | Answer | Detail |
|----------|--------|--------|
| Does traffic work? | yes | 2/2 pass |
| Is the root cause gone? | yes | A3 GigabitEthernet0/3 access VLAN 10 (want 10) |
| Is the fix saved? | yes | running matches startup |
| Is the design intact? | yes | intact |
| Does the section match the original? | yes | matches the original |

What you changed in the faulted sections, read from the running config at grading:

```diff
  ! A3, interface GigabitEthernet0/3
- switchport access vlan 30
+ switchport access vlan 10
```

### Where to look

| Step | Here |
|------|------|
| Symptom | E-2 is online, but it's sitting in the wrong department's network. |
| Scope | One host (E-2) has an address, but from the wrong subnet |
| Layer | Layer 2: VLANs and trunks |
| Device | A3 |
| Feature | A3 `interface GigabitEthernet0/3` |
| Clue | show interfaces Gi0/3 switchport on A3: 'Access Mode VLAN: 30' where the design says 10 |

### Method

- **Follow the path**: use when traffic dies somewhere along a path: traceroute, then routing, ARP and MAC tables hop by hop.
- **Compare known good**: use when a redundant peer works and its twin doesn't.

Applied here: The host leases an address, so DHCP, the relay and routing all work. The address comes from another VLAN's pool, which means the port drops the host into the wrong broadcast domain. Comparing the port with the design, or with a working host port, shows the access VLAN differs.

### Commands

Show commands (on A3):

```text
show interfaces Gi0/3 switchport
show vlan brief
show mac address-table interface Gi0/3
show running-config interface GigabitEthernet0/3
```

Debug, scoped and one at a time, then switch it off:

```text
terminal monitor
debug ip dhcp server packet
! on the DHCP server: the DISCOVER arrives via the VLAN 30 relay address
undebug all
```

The line that gives it away:

```text
A3# show interfaces Gi0/3 switchport
Access Mode VLAN: 30 (Service)
```

Quoted from [`evidence/broken-state-fault1.txt`](evidence/broken-state-fault1.txt), captured before the clock started.

## Collateral

None. Every baseline check that passed at the start still passed at grading.

## Takeaways

- Solved without hints: fault 1 (Host port in the wrong access VLAN).

### In my own words

1. Started at the host. `ip a` on E-2 showed `10.1.30.12/24`, an address from VLAN 30's subnet, so E-2 was online but on the wrong network, as the ticket said.
2. E-2's switch port, A3 Gi0/3, was in access VLAN 30. Changed it to `switchport access vlan 10` and saved the config.
3. Rebooted E-2 so it asked DHCP for a new lease. It came back with `10.1.10.12/24`.
4. Pinged `10.1.10.11` in the same VLAN: 3 of 3 replies.

## Evidence

| File | What it holds |
|------|---------------|
| [`ticket.md`](ticket.md) | The ticket as issued |
| [`broken.yaml`](broken.yaml) | The lab exactly as imported, faults included (sanitized) |
| [`answers.json`](answers.json) | Every fault instance: target, injected change, checks, reference fix |
| [`grade.json`](grade.json) | Raw check results for every fault, plus the baseline rerun for collateral |
| [`baseline_before.json`](baseline_before.json) | The baseline at the start of the session (collateral is judged against it) |
| [`hints.log`](hints.log) | Hints taken, timestamped |
| [`evidence/broken-state-fault1.txt`](evidence/broken-state-fault1.txt) | Fault 1 show commands in the broken state, captured at start |
| [`evidence/fault1-functional-1.txt`](evidence/fault1-functional-1.txt) | Fault 1 functional: host_lease E-2, PASS (E-2 has 10.1.10.12) |
| [`evidence/fault1-functional-2.txt`](evidence/fault1-functional-2.txt) | Fault 1 functional: ping from E-2, PASS (1/1 reachable on-link) |
| [`evidence/fault1-root_cause-1.txt`](evidence/fault1-root_cause-1.txt) | Fault 1 root cause: access_vlan A3 GigabitEthernet0/3, PASS (A3 GigabitEthernet0/3 access VLAN 10 (want 10)) |

---

*Generated by `python -m breaker debrief` from the live checks in `grade.json`. Grades come from device state read through pyATS, never from config text diffs.*
