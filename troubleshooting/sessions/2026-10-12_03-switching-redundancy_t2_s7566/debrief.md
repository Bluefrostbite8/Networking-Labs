# Debrief 03-t2-s7566: Redundant Campus Access Layer

![Score](https://img.shields.io/badge/Score-100%2F100-047857)
![Grades](https://img.shields.io/badge/Grades-Exact-047857)
![Tier](https://img.shields.io/badge/Tier-T2%20Standard-b45309)
![Hints](https://img.shields.io/badge/Hints-0-6d28d9)
![Time](https://img.shields.io/badge/Time-16.6%20min-0b7285)

The debrief for [ticket](ticket.md) `03-t2-s7566` on lab 03 Redundant Campus Access Layer: 1 fault, seed 7566, graded 2026-10-12 21:53.

## Results

| # | Fault | Grade | Score | Hints | Time |
|---|-------|-------|-------|-------|------|
| 1 | OSPF area mismatch on one end of a link | Exact | 100 | none | 16.6 min |

Session score **100**: the average of the fault scores (100 × grade weight, minus hint penalties and 15 per collateral failure, never below 0). Time runs from `start` to `grade`; every fault is graded in the same pass.

## Fault 1: OSPF area mismatch on one end of a link

**Ticket:** Monitoring shows the network lost one of its redundant routing paths overnight. Users haven't noticed yet.

### What was broken

`ospf-area-mismatch` (ospf, tier 2) was injected into the imported lab on R1, saved to startup so a reload would not undo it.

```diff
  ! R1, router ospf 1
- network 192.168.1.0 0.0.0.3 area 0
+ network 192.168.1.0 0.0.0.3 area 1
```

### Your fix

**Exact**, score 100. Traffic works, the root cause is gone by state, the fix is saved, the design is intact, and the changed section matches the original line for line.

| Question | Answer | Detail |
|----------|--------|--------|
| Does traffic work? | yes | 1/1 pass |
| Is the root cause gone? | yes | R1 GigabitEthernet0/1 in area 0.0.0.0 (want 0.0.0.0) |
| Is the fix saved? | yes | running matches startup |
| Is the design intact? | yes | intact |
| Does the section match the original? | yes | matches the original |

What you changed in the faulted sections, read from the running config at grading:

```diff
  ! R1, router ospf 1
- network 192.168.1.0 0.0.0.3 area 1
+ network 192.168.1.0 0.0.0.3 area 0
```

### Where to look

| Step | Here |
|------|------|
| Symptom | Monitoring shows the network lost one of its redundant routing paths overnight. Users haven't noticed yet. |
| Scope | One adjacency (R1 to Core1); traffic still flows over the redundant path |
| Layer | Layer 3: OSPF |
| Device | R1 |
| Feature | R1 `router ospf 1` |
| Clue | R1 logs '%OSPF-4-ERRRCV: Received invalid packet: mismatched area ID from backbone area from 192.168.1.2, GigabitEthernet0/1' |

### Method

- **Divide and conquer**: use when the ticket is vague: start at Layer 3 with ping, then move up or down.
- **Compare known good**: use when a redundant peer works and its twin doesn't.

Applied here: Traffic still flows over the redundant path, so this is lost redundancy, not an outage. The link between R1 and Core1 is up and pings, but there is no neighbor: hellos arrive and are rejected, so compare the hello parameters on both ends.

### Commands

Show commands (on R1):

```text
show ip ospf neighbor
show ip ospf interface brief
show ip protocols
show running-config | section router ospf
show logging | include OSPF
```

Debug, scoped and one at a time, then switch it off:

```text
terminal monitor
debug ip ospf adj
debug ip ospf hello
undebug all
```

The line that gives it away:

```text
R1# show logging | include OSPF
*Oct 13 02:24:56.126: %OSPF-4-ERRRCV: Received invalid packet: mismatched area ID from backbone area from 192.168.1.2, GigabitEthernet0/1
```

Quoted from [`evidence/broken-state-fault1.txt`](evidence/broken-state-fault1.txt), captured before the clock started.

## Collateral

None. Every baseline check that passed at the start still passed at grading.

## Takeaways

- Solved without hints: fault 1 (OSPF area mismatch on one end of a link).

### In my own words

1. Started by working out which redundant path was actually missing.
2. Checked the Layer 2 switches first: every interface was up and operational.
3. Moved up to the Layer 3 switches and the router. Checked that CDP neighbors and OSPF adjacencies were FULL and matched the real cabling. Core1 had only one OSPF neighbor, Core2, where it should also have had R1.
4. Checked Core1's OSPF configuration: nothing wrong on that side.
5. Found the cause on R1: the `network` statement for the Core1 link (192.168.1.0/30) put it in area 1 instead of area 0.
6. Replaced it with `network 192.168.1.0 0.0.0.3 area 0` and saved. The R1 to Core1 adjacency came back FULL.

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
| [`evidence/fault1-functional-1.txt`](evidence/fault1-functional-1.txt) | Fault 1 functional: ospf_neighbor R1 2.2.2.2, PASS (R1 sees 2.2.2.2 FULL/  -) |
| [`evidence/fault1-intent-1.txt`](evidence/fault1-intent-1.txt) | Fault 1 intent: ospf_neighbors R1, PASS (2 FULL of 2 (expected 2)) |
| [`evidence/fault1-intent-2.txt`](evidence/fault1-intent-2.txt) | Fault 1 intent: ospf_neighbors Core1, PASS (4 FULL of 4 (expected 4)) |
| [`evidence/fault1-intent-3.txt`](evidence/fault1-intent-3.txt) | Fault 1 intent: ospf_neighbors Core2, PASS (4 FULL of 4 (expected 4)) |
| [`evidence/fault1-root_cause-1.txt`](evidence/fault1-root_cause-1.txt) | Fault 1 root cause: ospf_interface_area R1 GigabitEthernet0/1, PASS (R1 GigabitEthernet0/1 in area 0.0.0.0 (want 0.0.0.0)) |

---

*Generated by `python -m breaker debrief` from the live checks in `grade.json`. Grades come from device state read through pyATS, never from config text diffs.*
