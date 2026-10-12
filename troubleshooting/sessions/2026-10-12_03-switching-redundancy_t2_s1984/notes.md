**How I worked ticket 03-t2-s1984**

1. Pinged between hosts in different VLANs to confirm connectivity was still up, so this was not an outage.
2. Ran a traceroute: VLAN 10 traffic went through Core2, but the design says Core1.
3. Ran `show standby brief` and `show standby vlan 10` on Core1: it was Standby with priority 0.
4. Set `standby 0 priority 255` on Core1's Vlan10 and saved. Core1 took Active back.
5. Missed: the fault had also added `standby 0 preempt` on Core2. That line is harmless here but not part of the design.