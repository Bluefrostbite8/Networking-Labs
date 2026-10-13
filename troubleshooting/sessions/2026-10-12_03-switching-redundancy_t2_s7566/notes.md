1. Started by working out which redundant path was actually missing.
2. Checked the Layer 2 switches first: every interface was up and operational.
3. Moved up to the Layer 3 switches and the router. Checked that CDP neighbors and OSPF adjacencies were FULL and matched the real cabling. Core1 had only one OSPF neighbor, Core2, where it should also have had R1.
4. Checked Core1's OSPF configuration: nothing wrong on that side.
5. Found the cause on R1: the `network` statement for the Core1 link (192.168.1.0/30) put it in area 1 instead of area 0.
6. Replaced it with `network 192.168.1.0 0.0.0.3 area 0` and saved. The R1 to Core1 adjacency came back FULL.
