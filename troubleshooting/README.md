# CML Break-Fix Troubleshooting — Fault Injection, Live Grading & Method Debriefs

![Platform](https://img.shields.io/badge/Platform-Cisco%20Modeling%20Labs%202.10-1BA0D7?logo=cisco&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![pyATS](https://img.shields.io/badge/Automation-pyATS%20%2B%20Genie%2025.x-005073)
![API](https://img.shields.io/badge/API-virl2--client%202.10.1-0b7285)
![Status](https://img.shields.io/badge/Status-Build%20step%206%20of%208-b45309)

## Objective

A tool that breaks a working lab from this repo, boots it in CML, grades the fix live through pyATS, and writes a debrief that teaches the troubleshooting method: where to look, which show and debug commands, and the output line that gives the fault away. Grading reads live device state only, never config text, so any valid fix counts.

## Status

| Build step | What it delivers | State |
|------------|------------------|-------|
| 1 | WSL1 environment and CML login with verified TLS | done |
| 2 | `lab_model.py`, `cml_api.py`: parse, import, boot, VLAN/VTP rebuild, clean | done, proven on lab 03 |
| 3 | Six lab profiles, each baseline passing on a healthy import | done, all six pass (table below) |
| 4 | `checks.py`, `grading.py`, first five faults on lab 03 | done, all five pass selftest on lab 03 |
| 5 | `report.py`: ticket and debrief, first published session | done 2026-10-12, session `03-t2-s1984` published |
| 6 | Local web console (`python -m breaker gui`) over the same commands | built 2026-10-12; first live session pending |
| 7 | Full library of 60 faults, each through `selftest` | |
| 8 | T4 combos and the methods pages | |

## Setup

Runs in WSL1 Ubuntu (pyATS does not run natively on Windows, and WSL2's hypervisor would stop VMware giving CML the VT-x it needs), from the repo on the Windows drive.

```text
cd /mnt/c/Users/Frost/Desktop/Networking/Networking-Labs/troubleshooting
source ~/.venvs/breaker/bin/activate
pip install -r requirements.txt
cp config.example.yaml config.yaml        # then set node_cap
```

Credentials come only from `VIRL2_URL`, `VIRL2_USER`, `VIRL2_PASS` and `CML_CA_BUNDLE`. TLS is always verified against the exported CML certificate.

## Usage

| Command | What it does |
|---------|--------------|
| `python -m breaker list` | The six labs, node counts, profiles and fault count |
| `python -m breaker show 03` | Parsed lab: nodes, config file, detected features, cabling, sanitizer and profile check |
| `python -m breaker doctor` | Credentials, TLS, login, and running nodes against the licence cap |
| `python -m breaker start 03 --healthy` | Import the unbroken lab as `<title> [incident <id>]`, boot it, rebuild VLANs/VTP, clear sticky MACs, renew leases, save, then run the baseline |
| `python -m breaker start 06 --healthy --clean-after` | Same, then stop, wipe and delete the lab whatever the outcome; the result stays in `runs/` |
| `python -m breaker baseline 03 --id <id>` | Run the profile's baseline checks live and save the evidence to `runs/` |
| `python -m breaker rebuild 03 --id <id>` | Rerun the rebuild on an incident lab that is already running (no import, no boot) |
| `python -m breaker console 04 WLC --id <id>` | Print and save CML's console buffer for one node (what it showed before pyATS could log in) |
| `python -m breaker break 03 --tier 2 [--seed S]` | Pick faults that passed selftest on the lab, write a session: `broken.yaml`, `ticket.md` (complaint, lab overview, rules, known good), sealed answers |
| `python -m breaker start <incident>` | Import the broken lab, rebuild, save each fault's broken-state output to `evidence/`, record the starting baseline; the clock starts |
| `python -m breaker hint <incident> <fault#>` | Unlock the next hint for one fault (in order, logged, penalised) |
| `python -m breaker grade <incident>` | Grade every fault live, rerun the baseline for collateral, save `grade.json` and `evidence/` |
| `python -m breaker debrief <incident>` | Reveal the answers (`answers.json` in plain text), sanitize, write `debrief.md`, add the session to the index below |
| `python -m breaker selftest <fault> [<fault> ...] --lab 03` or `--all` | Acceptance test, all faults in one import: inject, symptoms, reference fixes grade Exact, workarounds grade Temporary, then clean up |
| `python -m breaker clean [--id <id>] [--remove]` | Stop and wipe incident labs only (optionally delete them) |
| `python -m breaker batch --lab 03 --mark [--hours 9]` | Overnight: selftest every unproven fault that fits the lab, one import each, in a time budget; writes `runs/batch-*.md` and marks passing faults proven. Also on the GUI's Fault library page |
| `python -m breaker gui [--port 5050]` | Local web console: open `http://localhost:5050` in the Windows browser. Sessions, ticket and debrief, Start/Hint/Grade/Debrief/Clean buttons with a live log, the fault library with selftest |

## Healthy Baselines

Each lab imported unbroken, rebuilt and checked live. Evidence for every check is saved in `runs/` (gitignored).

| Lab | Checks | Result | What the baseline covers |
|-----|--------|--------|--------------------------|
| 01 Switching Fundamentals | 12 | pass | Per-VLAN STP roots, VLANs, trunks, host to host in VLAN 10 |
| 02 Multi-Area OSPF Campus | 28 | pass | OSPF adjacencies per area, VLANs, trunks, DHCP relay leases, site pings |
| 03 Switching Redundancy | 39 | pass | VTP v3, STP roots, HSRP, OSPF, EtherChannel, port security, DHCP |
| 04 Enterprise Wireless | 21 | pass | HSRP, LACP to the 9800, VLANs, trunks, DHCP, WLAN up, routed pings |
| 05 MST and PVST+ | 29 | pass | MST region and map, boundary ports, root guard, rapid-pvst, cross-boundary and routed pings |
| 06 EIGRP Classic and Named | 14 | pass | EIGRP neighbors, route sources and AD, DHCP, pings to leases |

### Platform behaviour the tool handles

| Behaviour | Seen on | What the tool does |
|-----------|---------|--------------------|
| Console ignores input while RSA keys generate after boot | IOSvL2 | Gives each attempt about 30 s and keeps retrying until the boot timeout |
| Some console sessions come up dead: the console server attaches, then not one byte arrives | CML console server | Abandons a silent session after about 30 s and opens a fresh one, which answers at once; a connect over 20 s keeps unicon's log in `runs/` |
| IOS-XE runs on the VGA console on a fresh import (`platform console serial` applies from the second boot) | Catalyst 9800-CL | Waits until DC-1 pings the WLC, power-cycles it once, then connects on serial |
| New VLANs leave ports in listening/learning; SVIs, HSRP and DHCP wait on them | IOSvL2 (PVST, MST) | Waits for `show spanning-tree summary` to show nothing in transition before checks |
| An SVI whose VLAN was auto-created at boot stays down/down after the VLAN is active | IOSvL2 | Bounces only those SVIs after spanning tree settles, and logs each one |
| No `clear port-security sticky` exec command | IOSvL2 15.2 | Removes the saved MAC under the interface instead |

## Safety

- Only labs whose title ends in ` [incident <id>]` are ever stopped, wiped or deleted; the title is re-read from the server before each step.
- `start` checks running nodes against `node_cap` first and lists which labs to stop instead of failing halfway.
- pyATS connects with no init config, so `line con 0` and the VTY lines are never rewritten by the tool.
- External connectors and unmanaged switches are never configured and never counted against the licence.

## Files

| Path | Description |
|------|-------------|
| `breaker/lab_model.py` | Topology parsing, config file per `node_definition`, config sections, cabling, sanitizer guard, lab profiles |
| `breaker/cml_api.py` | Login, capacity check, import, boot, console access, VLAN/VTP rebuild, stop and wipe |
| `breaker/checks.py` | Live state checks: Genie parsers first, regex fallback, evidence kept per check |
| `breaker/faults.py` | Fault library: load, acceptance checks, pick by tier and seed, render templates |
| `breaker/inject_static.py` | Fault targets (where a fault fits) and actions (the YAML edit before import) |
| `breaker/grading.py` | Grade decision order, persistence and original-section compare, collateral, scoring |
| `breaker/session.py` | Session folder: broken lab, sealed answers (revealed at the debrief), hints log |
| `breaker/report.py` | `ticket.md` and `debrief.md` in this repo's README style, the sanitizer pass, the session index |
| `faults/` | One YAML per fault, by category; `selftest_passed` lists the labs it is proven on |
| `breaker/cli.py` | Command line entry point |
| `breaker/gui/` | Flask web console: runs the CLI commands as background jobs (logs in `runs/gui-jobs/`), renders tickets and debriefs, hides anything that names a fault until its debrief. Fonts are bundled (SIL OFL) so it works offline |
| `runs/` | Per-run log, result and baseline evidence (gitignored) |
| `sessions/` | Published break-fix sessions: ticket, debrief, broken lab, answers, grade, evidence, hints |
| `labs/` | One profile per lab: VLANs and VTP to rebuild, design intent, baseline |
| `requirements.txt` | Pinned dependencies |
| `config.example.yaml` | Local options template (`config.yaml` is gitignored) |

## Session Index

Published break-fix sessions, newest first. Each links to its debrief.

| Date | Session | Lab | Tier | Faults | Score | Grades |
|------|---------|-----|------|--------|-------|--------|
| 2026-10-18 | [03-t2-s6466](sessions/2026-10-18_03-switching-redundancy_t2_s6466/debrief.md) | 03 Redundant Campus Access Layer | T2 | 1 | 100 | Exact |
| 2026-10-18 | [03-t2-s6224](sessions/2026-10-18_03-switching-redundancy_t2_s6224/debrief.md) | 03 Redundant Campus Access Layer | T2 | 2 | 70 | Temporary, Exact |
| 2026-10-12 | [03-t2-s7566](sessions/2026-10-12_03-switching-redundancy_t2_s7566/debrief.md) | 03 Redundant Campus Access Layer | T2 | 1 | 100 | Exact |
| 2026-10-12 | [03-t2-s7011](sessions/2026-10-12_03-switching-redundancy_t2_s7011/debrief.md) | 03 Redundant Campus Access Layer | T2 | 1 | 100 | Exact |
| 2026-10-12 | [03-t2-s1984](sessions/2026-10-12_03-switching-redundancy_t2_s1984/debrief.md) | 03 Redundant Campus Access Layer | T2 | 1 | 100 | Equivalent |

