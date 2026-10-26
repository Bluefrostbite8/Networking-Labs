# Debrief 02-t2-s7060: Multi-Area OSPF Enterprise Campus

![Score](https://img.shields.io/badge/Score-100%2F100-047857)
![Grades](https://img.shields.io/badge/Grades-Exact-047857)
![Tier](https://img.shields.io/badge/Tier-T2%20Standard-b45309)
![Hints](https://img.shields.io/badge/Hints-0-6d28d9)
![Time](https://img.shields.io/badge/Time-43.0%20min-0b7285)

The debrief for [ticket](ticket.md) `02-t2-s7060` on lab 02 Multi-Area OSPF Enterprise Campus: 1 fault, seed 7060, graded 2026-10-25 23:02.

## Results

| # | Fault | Grade | Score | Hints | Time |
|---|-------|-------|-------|-------|------|
| 1 | DHCP relays point at an address nobody answers | Exact | 100 | none | 43.0 min |

Session score **100**: the average of the fault scores (100 × grade weight, minus hint penalties and 15 per collateral failure, never below 0). Time runs from `start` to `grade`; every fault is graded in the same pass.

## Fault 1: DHCP relays point at an address nobody answers

**Ticket:** Nobody on the VLAN 10 floor can get on the network this morning.

### What was broken

`svc-helper-wrong` (services, tier 2) was injected into the imported lab on DC-S1, DC-S3, saved to startup so a reload would not undo it.

```diff
  ! DC-S1, interface Vlan10
- ip helper-address 10.4.10.2
+ ip helper-address 10.2.10.10
```

```diff
  ! DC-S3, interface Vlan10
- ip helper-address 10.3.10.2
+ ip helper-address 10.2.10.10
```

### Your fix

**Exact**, score 100. Traffic works, the root cause is gone by state, the fix is saved, the design is intact, and the changed section matches the original line for line.

| Question | Answer | Detail |
|----------|--------|--------|
| Does traffic work? | yes | 2/2 pass |
| Is the root cause gone? | yes | DC-S1: 'Helper address is 10.4.10.2'; DC-S3: 'Helper address is 10.3.10.2' |
| Is the fix saved? | yes | running matches startup |
| Is the design intact? | yes | intact |
| Does the section match the original? | yes | matches the original |

What you changed in the faulted sections, read from the running config at grading:

```diff
  ! DC-S1, interface Vlan10
- ip helper-address 10.2.10.10
+ ip helper-address 10.4.10.2
```

```diff
  ! DC-S3, interface Vlan10
- ip helper-address 10.2.10.10
+ ip helper-address 10.3.10.2
```

### Where to look

| Step | Here |
|------|------|
| Symptom | Nobody on the VLAN 10 floor can get on the network this morning. |
| Scope | Every host in VLAN 10: no address at all, other VLANs fine |
| Layer | Services: DHCP and NAT |
| Device | DC-S1 |
| Feature | DC-S1 `interface Vlan10`, DC-S3 `interface Vlan10` |
| Clue | show ip interface Vlan10 on DC-S1: 'Helper address is 10.2.10.10' where the server is 10.4.10.2 |

### Method

- **Follow the path**: use when traffic dies somewhere along a path: traceroute, then routing, ARP and MAC tables hop by hop.
- **Compare known good**: use when a redundant peer works and its twin doesn't.

Applied here: Only one VLAN is affected and its hosts get no address at all, so DHCP never completes. The server sits on another subnet, so each DISCOVER depends on the VLAN's gateways relaying it. Both relays point at 10.2.10.10, an address inside the VLAN that no device owns, so the requests go nowhere. Compare with a VLAN that works: its helper is the server's address.

### Commands

Show commands (on DC-S1):

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
! on DHCP : no DISCOVER arrives from VLAN 10
undebug all
```

The line that gives it away:

```text
DC-S1# show ip interface Vlan10
  Helper address is 10.2.10.10
```

Quoted from [`evidence/broken-state-fault1.txt`](evidence/broken-state-fault1.txt), captured before the clock started.

## Collateral

None. Every baseline check that passed at the start still passed at grading.

## Takeaways

- Solved without hints: fault 1 (DHCP relays point at an address nobody answers).

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
| [`evidence/fault1-functional-1.txt`](evidence/fault1-functional-1.txt) | Fault 1 functional: host_lease U-D3, PASS (U-D3 has 10.2.10.11) |
| [`evidence/fault1-functional-2.txt`](evidence/fault1-functional-2.txt) | Fault 1 functional: ping from U-D3, PASS (1/1 reachable) |
| [`evidence/fault1-root_cause-1.txt`](evidence/fault1-root_cause-1.txt) | Fault 1 root cause: DHCP relay on Vlan10 DC-S1, PASS (DC-S1: 'Helper address is 10.4.10.2') |
| [`evidence/fault1-root_cause-2.txt`](evidence/fault1-root_cause-2.txt) | Fault 1 root cause: DHCP relay on Vlan10 DC-S3, PASS (DC-S3: 'Helper address is 10.3.10.2') |

---

*Generated by `python -m breaker debrief` from the live checks in `grade.json`. Whether a fault is fixed comes from device state read through pyATS; config text is only compared to check that the fix was saved and to show what changed.*
