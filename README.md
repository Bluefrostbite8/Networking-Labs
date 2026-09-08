# Network Labs

Hands-on networking labs built in Cisco Modeling Labs (CML). Each lab includes the
topology export, device configurations, and a writeup covering the design, the
verification steps, and the problems I hit along the way.

CCNA certified.

## Why this repo exists

Configuring a protocol once from a tutorial doesn't teach much. Breaking it and
figuring out why it broke does. Every lab here includes a troubleshooting section
documenting what actually went wrong and how I isolated it. That's the part I find
most useful to write down, and the part I'd want to talk through in an interview.

## Repo structure

Each lab folder follows the same layout:

```
0X-lab-name/
├── README.md          # objective, topology, addressing, verification, troubleshooting
├── topology.yaml      # CML topology export, import directly into CML
├── topology.png       # screenshot or diagram
└── configs/
    ├── R1.txt
    ├── R2.txt
    └── SW1.txt
```

To run a lab yourself: download `topology.yaml`, then in CML choose
**Import** and select the file. Device configs are embedded in the export, and also
included separately in `configs/` for reading without CML.

## Environment

- **Cisco Modeling Labs** (personal edition)
- Node types: IOSv, IOSvL2, IOSv-L3, [add others you use]
- Diagrams: [draw.io / CML screenshots]

## Contact

[Your Name], [city, state]
[LinkedIn URL] · [email]
