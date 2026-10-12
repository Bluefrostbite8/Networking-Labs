1. Pinged between hosts in different VLANs to confirm connectivity was still up, so this was not an outage.
2. Ran a traceroute: VLAN 30 traffic went through Core2, but the design says Core1.
3. Ran `show standby brief` and `show standby vlan 30` on Core1: it was Standby with priority 0.
4. Set `standby 0 priority 255` on Core1's Vlan10 and saved. Core1 took Active back.
