# Network Labs

Hands-on networking labs built in Cisco Modeling Labs (CML). Each lab includes the
topology export, lab/device configurations, and a writeup covering the design, the
verification steps, and the problems I hit along the way.

CCNA certified (Sept 2026), CCNP ENCOR in progress.

## Why this repo exists

Configuring a protocol once from a tutorial doesn't teach much. Breaking it and
figuring out why it broke does. Every lab here includes a troubleshooting section
documenting what actually went wrong and how I isolated it. That's the part I find
most useful to write down, and the part I'd want to talk through in an interview.

## Repo structure

Each lab folder follows the same layout:

```
0X-lab-name/
├── README.md          # Troubleshooting, objective, topology, addressing
├── topology.yaml      # CML topology export, import directly into CML
├── topology.svg       # screenshot or diagram

```

To run a lab yourself: download `topology.yaml`, then in CML choose
**Import** and select the file. Device configs are embedded in the export.

## Environment

- **Cisco Modeling Labs** (personal edition)
- Node types: IOSv, IOSvL2, IOSv-L3, IOL-XE

## Contact

Roman Schroeder, Kyle, Tx
www.linkedin.com/in/roman-schroeder-856188410/ · schroedermroman@gmail.com
