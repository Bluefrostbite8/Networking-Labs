# Debrief 03-t2-s6224: Redundant Campus Access Layer

![Score](https://img.shields.io/badge/Score-70%2F100-b45309)
![Grades](https://img.shields.io/badge/Grades-Temporary%20%C2%B7%20Exact-b45309)
![Tier](https://img.shields.io/badge/Tier-T2%20Standard-b45309)
![Hints](https://img.shields.io/badge/Hints-0-6d28d9)
![Time](https://img.shields.io/badge/Time-not%20recorded-0b7285)

The debrief for [ticket](ticket.md) `03-t2-s6224` on lab 03 Redundant Campus Access Layer: 2 faults, seed 6224, graded 2026-10-18 16:32.

## Results

| # | Fault | Grade | Score | Hints | Time |
|---|-------|-------|-------|-------|------|
| 1 | DHCP relays point at an address nobody answers | Temporary | 40 | none |  |
| 2 | Host port in the wrong access VLAN | Exact | 100 | none |  |

Session score **70**: the average of the fault scores (100 × grade weight, minus hint penalties and 15 per collateral failure, never below 0). Time runs from `start` to `grade`; every fault is graded in the same pass.

## Fault 1: DHCP relays point at an address nobody answers

**Ticket:** Nobody on the VLAN 10 floor can get on the network this morning.

### What was broken

`svc-helper-wrong` (services, tier 2) was injected into the imported lab on Core1, Core2, saved to startup so a reload would not undo it.

```diff
  ! Core1, interface Vlan10
- ip helper-address 192.168.1.1
+ ip helper-address 10.1.10.10
```

```diff
  ! Core2, interface Vlan10
- ip helper-address 192.168.1.5
+ ip helper-address 10.1.10.10
```

### Your fix

**Temporary**, score 40. Traffic works, but the root cause is still there (Core1: 'Helper address is 192.168.1.1'; Core2: no line matches (expected one)).

| Question | Answer | Detail |
|----------|--------|--------|
| Does traffic work? | yes | 2/2 pass |
| Is the root cause gone? | **no** | Core1: 'Helper address is 192.168.1.1'; Core2: no line matches (expected one) |

What you changed in the faulted sections, read from the running config at grading:

```diff
  ! Core1, interface Vlan10
- ip helper-address 10.1.10.10
+ ip helper-address 192.168.1.1
```

Still different from the original lab after your fix (not wrong, but not the design's config either; worth tidying on a real network):

```diff
  ! Core2, interface Vlan10
- ip helper-address 192.168.1.5
+ ip helper-address 10.1.10.10
```

### Where to look

| Step | Here |
|------|------|
| Symptom | Nobody on the VLAN 10 floor can get on the network this morning. |
| Scope | Every host in VLAN 10: no address at all, other VLANs fine |
| Layer | Services: DHCP and NAT |
| Device | Core1 |
| Feature | Core1 `interface Vlan10`, Core2 `interface Vlan10` |
| Clue | show ip interface Vlan10 on Core1: 'Helper address is 10.1.10.10' where the server is 192.168.1.1 |

### Method

- **Follow the path**: use when traffic dies somewhere along a path: traceroute, then routing, ARP and MAC tables hop by hop.
- **Compare known good**: use when a redundant peer works and its twin doesn't.

Applied here: Only one VLAN is affected and its hosts get no address at all, so DHCP never completes. The server sits on another subnet, so each DISCOVER depends on the VLAN's gateways relaying it. Both relays point at 10.1.10.10, an address inside the VLAN that no device owns, so the requests go nowhere. Compare with a VLAN that works: its helper is the server's address.

### Commands

Show commands (on Core1):

```text
show ip interface Vlan10
show running-config interface Vlan10
show ip interface brief
show standby brief
```

Debug, scoped and one at a time, then switch it off:

```text
terminal monitor
debug ip dhcp server packet
! on R1: no DISCOVER arrives from VLAN 10
undebug all
```

The line that gives it away:

```text
Core1# show ip interface Vlan10
  Helper address is 10.1.10.10
```

Quoted from [`evidence/broken-state-fault1.txt`](evidence/broken-state-fault1.txt), captured before the clock started.

### Better fix

Point both relays back at the DHCP server and save with 'write memory' on Core1 and Core2.

```text
! Core1
interface Vlan10
 no ip helper-address 10.1.10.10
 ip helper-address 192.168.1.1
! then on Core1: write memory
! Core2
interface Vlan10
 no ip helper-address 10.1.10.10
 ip helper-address 192.168.1.5
! then on Core2: write memory
```

## Fault 2: Host port in the wrong access VLAN

**Ticket:** M-1 is online, but it's sitting in the wrong department's network.

### What was broken

`l2-access-vlan` (l2, tier 1) was injected into the imported lab on A2, saved to startup so a reload would not undo it.

```diff
  ! A2, interface GigabitEthernet0/0
- switchport access vlan 20
+ switchport access vlan 30
```

### Your fix

**Exact**, score 100. Traffic works, the root cause is gone by state, the fix is saved, the design is intact, and the changed section matches the original line for line.

| Question | Answer | Detail |
|----------|--------|--------|
| Does traffic work? | yes | 2/2 pass |
| Is the root cause gone? | yes | A2 GigabitEthernet0/0 access VLAN 20 (want 20) |
| Is the fix saved? | yes | running matches startup |
| Is the design intact? | yes | intact |
| Does the section match the original? | yes | matches the original |

What you changed in the faulted sections, read from the running config at grading:

```diff
  ! A2, interface GigabitEthernet0/0
- switchport access vlan 30
+ switchport access vlan 20
```

### Where to look

| Step | Here |
|------|------|
| Symptom | M-1 is online, but it's sitting in the wrong department's network. |
| Scope | One host (M-1) has an address, but from the wrong subnet |
| Layer | Layer 2: VLANs and trunks |
| Device | A2 |
| Feature | A2 `interface GigabitEthernet0/0` |
| Clue | show interfaces Gi0/0 switchport on A2: 'Access Mode VLAN: 30' where the design says 20 |

### Method

- **Follow the path**: use when traffic dies somewhere along a path: traceroute, then routing, ARP and MAC tables hop by hop.
- **Compare known good**: use when a redundant peer works and its twin doesn't.

Applied here: The host leases an address, so DHCP, the relay and routing all work. The address comes from another VLAN's pool, which means the port drops the host into the wrong broadcast domain. Comparing the port with the design, or with a working host port, shows the access VLAN differs.

### Commands

Show commands (on A2):

```text
show interfaces Gi0/0 switchport
show vlan brief
show mac address-table interface Gi0/0
show running-config interface GigabitEthernet0/0
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
A2# show interfaces Gi0/0 switchport
Access Mode VLAN: 30 (Service)
```

Quoted from [`evidence/broken-state-fault2.txt`](evidence/broken-state-fault2.txt), captured before the clock started.

## Collateral

None. Every baseline check that passed at the start still passed at grading.

## Takeaways

- Solved without hints: fault 2 (Host port in the wrong access VLAN).
- Fault 1 graded Temporary: Traffic works, but the root cause is still there (Core1: 'Helper address is 192.168.1.1'; Core2: no line matches (expected one)).
- Methods that cracked more than one fault: follow the path (faults 1, 2); compare known good (faults 1, 2).

### In my own words

1. Started at a host on the VLAN 10 floor. `ip a` showed no IPv4 address at all, only a link-local IPv6 one, so the host never got a DHCP lease.
2. Checked the DHCP server first. `show run | s dhcp` on R1 showed the VLAN 10 pool intact (network `10.1.10.0/24`, gateway `10.1.10.3`), so the problem sat between the host and the server: the relay.
3. `show run | s Vlan` on Core1 showed Vlan10 relaying to `10.1.10.10`, while Vlan20 and Vlan30 relay to R1 at `192.168.1.1`. Added `ip helper-address 192.168.1.1` and removed `ip helper-address 10.1.10.10` on Vlan10.
4. For complaint 2, `ip a` on M-1 showed `10.1.30.12/24`, an address from VLAN 30's subnet. M-1's port, A2 Gi0/0, was in access VLAN 30. Changed it to `switchport access vlan 20` and saved.
5. What I missed: the relay fault was on both cores. Core2's Vlan10 also pointed at `10.1.10.10` instead of its own R1 address, `192.168.1.5`. VLAN 10 worked again because Core1 is the HSRP active gateway, but a failover to Core2 would have broken DHCP again. In a redundant pair, after finding a bug on one peer, check its twin before calling it fixed.

## Evidence

| File | What it holds |
|------|---------------|
| [`ticket.md`](ticket.md) | The ticket as issued |
| [`broken.yaml`](broken.yaml) | The lab exactly as imported, faults included (sanitized) |
| [`answers.json`](answers.json) | Every fault instance: target, injected change, checks, reference fix |
| [`grade.json`](grade.json) | Raw check results for every fault, plus the baseline rerun for collateral |
| [`hints.log`](hints.log) | Hints taken, timestamped |
| [`evidence/broken-state-fault1.txt`](evidence/broken-state-fault1.txt) | Fault 1 show commands in the broken state, captured at start |
| [`evidence/broken-state-fault2.txt`](evidence/broken-state-fault2.txt) | Fault 2 show commands in the broken state, captured at start |
| [`evidence/fault1-functional-1.txt`](evidence/fault1-functional-1.txt) | Fault 1 functional: host_lease E-1, PASS (E-1 has 10.1.10.11) |
| [`evidence/fault1-functional-2.txt`](evidence/fault1-functional-2.txt) | Fault 1 functional: ping from E-1, PASS (1/1 reachable) |
| [`evidence/fault1-root_cause-1.txt`](evidence/fault1-root_cause-1.txt) | Fault 1 root cause: DHCP relay on Vlan10 Core1, PASS (Core1: 'Helper address is 192.168.1.1') |
| [`evidence/fault1-root_cause-2.txt`](evidence/fault1-root_cause-2.txt) | Fault 1 root cause: DHCP relay on Vlan10 Core2, FAIL (Core2: no line matches (expected one)) |
| [`evidence/fault2-functional-1.txt`](evidence/fault2-functional-1.txt) | Fault 2 functional: host_lease M-1, PASS (M-1 has 10.1.20.11) |
| [`evidence/fault2-functional-2.txt`](evidence/fault2-functional-2.txt) | Fault 2 functional: ping from M-1, PASS (1/1 reachable on-link) |
| [`evidence/fault2-root_cause-1.txt`](evidence/fault2-root_cause-1.txt) | Fault 2 root cause: access_vlan A2 GigabitEthernet0/0, PASS (A2 GigabitEthernet0/0 access VLAN 20 (want 20)) |

---

*Generated by `python -m breaker debrief` from the live checks in `grade.json`. Grades come from device state read through pyATS, never from config text diffs.*
