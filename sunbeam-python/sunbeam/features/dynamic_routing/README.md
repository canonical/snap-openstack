# Neutron dynamic routing

The feature runs one idle DR agent per routing domain on each selected
MicroOVN network node. It requires NDR-enabled revisions
of the `neutron-k8s` and `openstack-network-agents` charms, the agent snap,
and a Neutron rock built from published NDR packages containing the
required route-event fixes. The nodes must already be joined to the
Sunbeam deployment and have the
`network` role. Neutron must use MicroOVN through the `sunbeam-ovn-proxy`
`ovsdb-cms` relation.

```yaml
features:
  dynamic-routing:
    config:
      routing-domains:
        red:
          peering:
            provider-trunk:
              physnet: physnet1
              vlan: 120
              nodes:
                net-01: {address: 192.0.2.3/28}
                net-02: {address: 192.0.2.4/28}
        blue:
          address-families: [ipv6]
          peering:
            dedicated-uplink:
              nodes:
                net-01: {interface: enp5s0.122}
                net-02: {interface: enp5s0.122}
```

Each domain accepts exactly one address family. Omitting `address-families`
selects IPv4. IPv4 and IPv6 domains can share the same network nodes.
Each attachment requires an explicit non-empty node map. For a provider
trunk, allow the specified VLAN on the fabric trunk and provide a static
CIDR for each node. For a dedicated uplink, provision the interface and
addressing before enablement. In Manual mode, give the interface name. In
MAAS, tag exactly one interface on each selected machine with
`ndr:<domain>` and omit the interface name. Specify a bare `address` when
the interface has more than one usable source in the chosen family.
Sunbeam validates the hosts and the Neutron schema before enabling NDR.

NDR is off by default. To opt in, run enable with your full deployment
manifest.

```bash
sunbeam enable --manifest deployment.yaml dynamic-routing
openstack bgp dragent list
```

Use each agent's unique host value, such as `net-01.red.v4`, to identify
its UUID. Set the fabric VRF and create and bind the peers and speaker
with `openstack bgp`. The NDR agent initiates the BGP connection. Configure
the fabric peer to accept connections to TCP port 179 from the agent's
peering address. The snap blocks Linux forwarding to and from NDR
peering interfaces. Tenant traffic uses the OVN gateway. For subnet-only
export, use
`--no-advertise-floating-ip-host-routes`. An IPv4 speaker can export both
subnets and centralized floating IPs.

Reapply the manifest after changing placement or joining a candidate node.
Sunbeam reports bound agents before removal. Unbind them first, or use the
feature's `--force` option to stop the agents while retaining their Neutron
bindings. Re-adding an identity can reactivate an existing binding. Coordinate
placement updates with OpenStack operators so bindings do not change during the
removal check and apply. Manual and MAAS node removal require the node
to be drained from NDR placement first, even when removing with `--force`.
Drain the node while its charm unit is still available.

Run `sunbeam disable dynamic-routing` to stop all managed
instances and disable the plugin. The host watcher remains available to
process committed snap settings when NDR is enabled again.

To change a domain's family or an agent's peering attachment or address,
unbind and remove the affected placement first. Reapply the manifest to
drain it, then add the new placement and bind the agents. An empty
`routing-domains: {}` map drains the last domain. Omitting the entire map
reuses saved placement.

Drain legacy prototype domains explicitly after refreshing the charm and
snap. The old flat `nodes` schema cannot select host attachments. Bind any
replacement agent that registers under a new UUID.

If a saved provider-trunk attachment can no longer be resolved, for example
because its physnet mapping disappeared, the snap stops the NDR agents on
that unit and retains the existing firewall protection. Restore the mapping
so the watcher can validate the placement and resume the agents.

Sunbeam assigns router IDs from `router-id-pool`, which defaults to
`198.18.0.0/15`, and retains its reservations across drain and re-enable
with the same pool. Omit the option to keep the saved pool. Disable NDR
before switching pools.

Sunbeam retrieves admin credentials from the deployment's Keystone and
uses the deployment's region to select the Neutron endpoint. IPv4 and
IPv6 each need their own Neutron BGP speaker and agent binding. IPv6 uses
a numbered IPv6 peer and next hop in the qualified topology. The BGP
router ID remains a 32-bit identifier.
The first rollout uses plain RabbitMQ transport. Release qualification includes
route freshness during RPC loss and forwarding through the selected fabric VRFs.
On IPv6 fabrics with multicast snooping, verify neighbor resolution after
cache expiry as part of release qualification.
