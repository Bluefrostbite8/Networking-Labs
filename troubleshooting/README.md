# CML Break-Fix Troubleshooting — Fault Injection, Live Grading & Method Debriefs

![Platform](https://img.shields.io/badge/Platform-Cisco%20Modeling%20Labs%202.10-1BA0D7?logo=cisco&logoColor=white)
![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)
![pyATS](https://img.shields.io/badge/Automation-pyATS%20%2B%20Genie%2025.x-005073)
![API](https://img.shields.io/badge/API-virl2--client%202.10.1-0b7285)
![Status](https://img.shields.io/badge/Status-Build%20step%202%20of%207-b45309)

## Objective

A tool that breaks a working lab from this repo, boots it in CML, grades the fix live through pyATS, and writes a debrief that teaches the troubleshooting method: where to look, which show and debug commands, and the output line that gives the fault away. Grading reads live device state only, never config text, so any valid fix counts.

## Status

| Build step | What it delivers | State |
|------------|------------------|-------|
| 1 | WSL2 environment and CML login with verified TLS | done |
| 2 | `lab_model.py`, `cml_api.py`: parse, import, boot, VLAN/VTP rebuild, clean | in testing on lab 03 |
| 3 | Six lab profiles, each baseline passing on a healthy import | |
| 4 | `checks.py`, `grading.py`, first five faults on lab 03 | |
| 5 | `report.py`: ticket and debrief, first published session | |
| 6 | Full library of 60 faults, each through `selftest` | |
| 7 | T4 combos and the methods pages | |

## Setup

Runs in WSL2 Ubuntu (pyATS does not run natively on Windows), from the repo on the Windows drive.

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
| `python -m breaker start 03 --healthy` | Import the unbroken lab as `<title> [incident <id>]`, boot it, rebuild VLANs/VTP, clear sticky MACs, renew leases, save |
| `python -m breaker rebuild 03 --id <id>` | Rerun the rebuild on an incident lab that is already running (no import, no boot) |
| `python -m breaker clean [--id <id>] [--remove]` | Stop and wipe incident labs only (optionally delete them) |

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
| `breaker/cli.py` | Command line entry point |
| `labs/` | One profile per lab: VLANs and VTP to rebuild, design intent, baseline |
| `requirements.txt` | Pinned dependencies |
| `config.example.yaml` | Local options template (`config.yaml` is gitignored) |

## Session Index

No sessions published yet.
