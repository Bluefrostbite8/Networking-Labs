# Debrief 03-t2-s7011: Redundant Campus Access Layer

![Score](https://img.shields.io/badge/Score-100%2F100-047857)
![Grades](https://img.shields.io/badge/Grades-Exact-047857)
![Tier](https://img.shields.io/badge/Tier-T2%20Standard-b45309)
![Hints](https://img.shields.io/badge/Hints-0-6d28d9)
![Time](https://img.shields.io/badge/Time-17.6%20min-0b7285)

The debrief for [ticket](ticket.md) `03-t2-s7011` on lab 03 Redundant Campus Access Layer: 1 fault, seed 7011, graded 2026-10-12 18:06.

## Results

| # | Fault | Grade | Score | Hints | Time |
|---|-------|-------|-------|-------|------|
| 1 | HSRP active on the wrong switch | Exact | 100 | none | 17.6 min |

Session score **100**: the average of the fault scores (100 × grade weight, minus hint penalties and 15 per collateral failure, never below 0). Time runs from `start` to `grade`; every fault is graded in the same pass.

## Fault 1: HSRP active on the wrong switch

**Ticket:** Monitoring shows VLAN 30 traffic leaving through a different first hop than the design.

### What was broken

`fhrp-priority-flip` (fhrp, tier 2) was injected into the imported lab on Core1, Core2, saved to startup so a reload would not undo it.

```diff
  ! Core1, interface Vlan30
- standby 2 priority 255
+ standby 2 priority 0
```

```diff
  ! Core2, interface Vlan30
+ standby 2 preempt
```

### Your fix

**Exact**, score 100. Traffic works, the root cause is gone by state, the fix is saved, the design is intact, and the changed section matches the original line for line.

| Question | Answer | Detail |
|----------|--------|--------|
| Does traffic work? | yes | 1/1 pass |
| Is the root cause gone? | yes | Core1 255 vs Core2 1 |
| Is the fix saved? | yes | running matches startup |
| Is the design intact? | yes | intact |
| Does the section match the original? | yes | matches the original |

What you changed in the faulted sections, read from the running config at grading:

```diff
  ! Core1, interface Vlan30
- standby 2 priority 0
+ standby 2 priority 255
```

```diff
  ! Core2, interface Vlan30
- standby 2 preempt
```

### Where to look

| Step | Here |
|------|------|
| Symptom | Monitoring shows VLAN 30 traffic leaving through a different first hop than the design. |
| Scope | VLAN 30's first hop; users still have a working gateway |
| Layer | Layer 3: first hop (HSRP) |
| Device | Core1 |
| Feature | Core1 `interface Vlan30`, Core2 `interface Vlan30` |
| Clue | show standby Vlan30 on Core1: 'Active router is 10.1.30.2, priority 1' while its own line reads 'Priority 0 (configured 0)' |

### Method

- **Compare known good**: use when a redundant peer works and its twin doesn't.
- **Follow the path**: use when traffic dies somewhere along a path: traceroute, then routing, ARP and MAC tables hop by hop.

Applied here: Users still have a gateway, so this is not an outage: HSRP elected the other router. The question is why the design active lost the election, so compare priority and preempt on both peers. Two changes work together here: the design active's priority dropped below its peer's, and the peer gained preempt, so it takes over even when it comes up second.

### Commands

Show commands (on Core1):

```text
show standby brief
show standby Vlan30
show running-config interface Vlan30
```

Debug, scoped and one at a time, then switch it off:

```text
terminal monitor
debug standby events
undebug all
```

The line that gives it away:

```text
Core1# show standby Vlan30
  Active router is 10.1.30.2, priority 1 (expires in 7.760 sec)
```

Quoted from [`evidence/broken-state-fault1.txt`](evidence/broken-state-fault1.txt), captured before the clock started.

## Collateral

None. Every baseline check that passed at the start still passed at grading.

## Takeaways

- Solved without hints: fault 1 (HSRP active on the wrong switch).

### In my own words

1. Pinged between hosts in different VLANs to confirm connectivity was still up, so this was not an outage.
2. Ran a traceroute: VLAN 30 traffic went through Core2, but the design says Core1.
3. Ran `show standby brief` and `show standby vlan 30` on Core1: it was Standby with priority 0.
4. Set `standby 2 priority 255` on Core1's Vlan30 and saved. Core1 took Active back.
5. Removed the `standby 2 preempt` the fault had added on Core2's Vlan30, so both cores match the design again.

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
| [`evidence/fault1-functional-1.txt`](evidence/fault1-functional-1.txt) | Fault 1 functional: hsrp_state Core1 Vlan30, PASS (Core1 Vlan30 is Active (want Active)) |
| [`evidence/fault1-intent-1.txt`](evidence/fault1-intent-1.txt) | Fault 1 intent: hsrp_active vlan 10, PASS (Core1 Vlan10 is Active) |
| [`evidence/fault1-intent-2.txt`](evidence/fault1-intent-2.txt) | Fault 1 intent: hsrp_active vlan 20, PASS (Core2 Vlan20 is Active) |
| [`evidence/fault1-intent-3.txt`](evidence/fault1-intent-3.txt) | Fault 1 intent: hsrp_active vlan 30, PASS (Core1 Vlan30 is Active) |
| [`evidence/fault1-root_cause-1.txt`](evidence/fault1-root_cause-1.txt) | Fault 1 root cause: hsrp_priority Core1>Core2 Vlan30, PASS (Core1 255 vs Core2 1) |

---

*Generated by `python -m breaker debrief` from the live checks in `grade.json`. Grades come from device state read through pyATS, never from config text diffs.*
