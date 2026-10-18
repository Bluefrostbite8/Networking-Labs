1. Started at the host. `ip a` on E-2 showed `10.1.30.12/24`, an address from VLAN 30's subnet, so E-2 was online but on the wrong network, as the ticket said.
2. E-2's switch port, A3 Gi0/3, was in access VLAN 30. Changed it to `switchport access vlan 10` and saved the config.
3. Rebooted E-2 so it asked DHCP for a new lease. It came back with `10.1.10.12/24`.
4. Pinged `10.1.10.11` in the same VLAN: 3 of 3 replies.
