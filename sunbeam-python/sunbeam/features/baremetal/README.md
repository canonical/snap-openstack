# Baremetal service

This feature provides Baremetal service for Sunbeam. It's based on [Ironic](https://docs.openstack.org/ironic/latest/), the bare metal provisioning service for OpenStack.

## Installation

To enable the Baremetal service, you need an already bootstrapped Sunbeam instance, and the storage role enabled. Then, you can install the feature with:

```bash
sunbeam enable baremetal
```

The feature will be configured based on the cluster's manifest file. Alternatively, a different manifest file can be specified during the feature enablement:

```bash
sunbeam enable --manifest baremetal-manifest.yaml baremetal
```

Sample `baremetal-manifest.yaml` file:

```yaml
features:
  baremetal:
    software:
      charms:
        ironic-conductor-k8s:
          channel: 2025.1/edge
        ironic-k8s:
          channel: 2025.1/edge
        nova-ironic-k8s:
          channel: 2025.1/edge
        neutron-baremetal-switch-config-k8s:
          channel: 2025.1/edge
        neutron-generic-switch-config-k8s:
          channel: 2025.1/edge
    config:
      shards: ["foo", "lish"]
      conductor-groups: ["foo", "lish"]
      switchconfigs:
        netconf:
          foo:
            configfile: |
              ["nexus.example.net"]
              driver = "netconf-openconfig"
              device_params = "name:nexus"
              switch_info = "nexus"
              switch_id = "00:53:00:0a:0a:0a"
              host = "nexus.example.net"
              username = "user"
              key_filename = "/etc/neutron/sshkeys/nexus-sshkey"
            additional-files:
              nexus-sshkey: |
                some key here.
        generic:
          lish:
            configfile: |
              ["genericswitch:arista-hostname"]
              device_type = "netmiko_arista_eos"
              ngs_mac_address = "00:53:00:0a:0a:0a"
              ip = "10.20.30.40"
              username = "admin"
              key_file = "/etc/neutron/sshkeys/arista-key"
            additional-files:
              arista-key: |
                some key here.
```

**Note**: Rerunning the `sunbeam enable baremetal` command with a different manifest file will replace the previously deployed feature configuration (e.g.: deployed `nova-ironic` shards, Ironic Conductor groups, Neutron switch configurations).

For the switch configurations, the following restrictions apply:

- The `key_filename` and `key_file` config options base file paths must be `/etc/neutron/sshkeys`.
- The files referenced in `key_filename` or `key_file` as seen above will require those files to be defined as additional files as well.
- Unknown fields in the switch configurations are not allowed. See [netconf configuration options](https://docs.openstack.org/networking-baremetal/2025.1/configuration/ml2/device_drivers/netconf-openconfig.html) or [generic switch configuration](https://docs.openstack.org/networking-generic-switch/2025.1/configuration.html)
- For `generic` switch configurations, the `device_type` field is mandatory.

After the feature is enabled, you can use the `sunbeam baremetal` subcommand to manage the deployed `nova-ironic` shards, Ironic Conductor groups, and Neutron switch configurations.

## Managing `nova-ironic` shards

`nova-ironic` shards will be deployed while enabling the `baremetal` feature, as mentioned above. Additional shards can be added through the following command:

```bash
sunbeam baremetal shard add SHARD
```

`nova-ironic` shards can be removed by running the following command:

```bash
sunbeam baremetal shard delete SHARD
```

The following command can be used to list the currently deployed shards:

```bash
sunbeam baremetal shard list
```

## Managing Ironic Conductor groups

By default, sunbeam deploys an `ironic-conductor-k8s` charm with an empty `conductor-group` configuration option. Additional Ironic Conductor groups will be deployed while enabling the `baremetal` feature, based on the `conductor-groups` configuration mentioned above.

Additional Ironic Conductor groups can be added through the following command:

```bash
sunbeam baremetal conductor-groups add GROUP-NAME
```

Ironic Conductor Groups can be removed by running the following command:

```bash
sunbeam baremetal conductor-groups delete GROUP-NAME
```

The following command can be used to list the currently Ironic Conductor
Groups:

```bash
sunbeam baremetal conductor-groups list
```

## Managing Neutron Switch Configurations

`netconf` and `generic` Neutron switch configurations will be added while enabling the `baremetal` feature, as mentioned above. Additional configurations can be added through the following command:

```bash
sunbeam baremetal switch-config add netconf|generic NAME --config CONFIGFILE  [--additional-file <NAME FILEPATH>]
```

An existing switch configuration can be updated with the command:

```bash
sunbeam baremetal switch-config update netconf|generic NAME --config CONFIGFILE  [--additional-file <NAME FILEPATH>]
```

For the add / update subcommands, multiple additional files can be specified.

Note that the same restrictions for the switch configurations mentioned above still apply when adding new ones or updating existing ones.

A switch configuration can be deleted with the following command:

```bash
sunbeam baremetal switch-config delete NAME
```

The following command can be used to list the current Neutron switch
configurations and their protocol:

```bash
sunbeam baremetal switch-config list
```

## Testing

Instead of physical machines, the baremetal feature can also be tested with virtual machines by using VirtualBMC. Below are instructions on how to setup such an environment.

### Prerequisites

This setup requires a node with the following prerequisites:

- the host must have sufficient resources to host virtual machines (VM size: 4GB RAM, 2 vCPUs, 40GB disk). This can be customized by editing the files `sample/create-machine.sh` and `sample/libvirt/machine1.xml` as needed.
- the host must be reachable by a Kubernetes Pod (the `ironic-conductor` Pod will send IPMI commands to it).
- the host's VM network must allow PXE / iPXE traffic, and OVN must be able to respond to DHCP requests on it. The VM's network can be customized by editing the file `sample/libvirt/machine1.xml`.
- the host's VM must be able to reach `ironic-conductor`'s LoadBalancer IP and the Swift service endpoint.

### Setup

The script `sample/create-machine.sh` can be run on a host that meets the prerequisites above. The script will perform the following actions:

- installs dependencies (`qemu-kvm`, `libvirt-daemon-system`, `libvirt-dev`, `ipmitool`, `virtualbmc`).
- creates a virtual machine.
- adds it to VirtualBMC.
- downloads the `tinyipa-master.vmlinuz` and `tinyipa-master.gz` images, registers into Glance, and copies them to the Swift store.
- creates the `metallic-flavor` Nova flavor that can be used to create a Nova server on the Ironic node.
- registers the VM as an Ironic node and configures it.
- registers a port for the Ironic node and configures it.
- brings the Ironic node to the `available` provisioning state, allowing it to be usable.

The `sample/create-machine.sh` script assumes admin credentials to be set in `~/.openrc`, and that the admin user has a system scoped role assigned. This can be checked by running the command:

```
openstack role assignment list --user USERNAME --names
```

A system-scoped role can be added to a user by running the following command:

```
openstack role add --system all --user USERNAME ROLE
```

The `sample/create-machine.sh` script can be executed:

```
./sample/create-machine.sh IPMI_ADDR IRONIC_NETWORK
```

The arguments of the scripts are as follows:

- `IPMI_ADDR`: IP address of the VirtualBMC host. This address should be reachable by a Kubernetes Pod (the `ironic-conductor` Pod will send IPMI commands to it).
- `IRONIC_NETWORK`: A flat or VLAN Neutron network used for provisioning. The network must provide DHCP and allow PXE / iPXE traffic. Instances on the network must be able to reach the `ironic-conductor`'s LoadBalancer IP and the Swift service endpoint.

### Creating an instance

After running the setup above, you can create an instance on the Ironic node:

```
GLANCE_IMAGE=""  # A Glance image backed by the Swift store (required by Ironic).
# If the $GLANCE_IMAGE is not in the Swift store, it can be imported into it by running the following command:
# openstack image import $GLANCE_IMAGE --method copy-image --store swift
openstack server create --image $GLANCE_IMAGE --flavor metallic-flavor \
  --nic net-id=$IRONIC_NETWORK --key your-keypair metallic-server
```

After a while, the Ironic node should have enter the `active` provisioning state, and the Nova server should have an `ACTIVE` status:

```
openstack baremetal node list
+--------------------------------------+----------+--------------------------------------+-------------+--------------------+-------------+
| UUID                                 | Name     | Instance UUID                        | Power State | Provisioning State | Maintenance |
+--------------------------------------+----------+--------------------------------------+-------------+--------------------+-------------+
| 12f9f792-2d7e-4447-929e-1e183798ecff | machine1 | 53a5efd0-acc5-41ac-a683-be67559ca743 | power on    | active             | False       |
+--------------------------------------+----------+--------------------------------------+-------------+--------------------+-------------+

openstack server list
+--------------------------------------+-----------------+--------+-------------------------------+--------------------------+-----------------+
| ID                                   | Name            | Status | Networks                      | Image                    | Flavor          |
+--------------------------------------+-----------------+--------+-------------------------------+--------------------------+-----------------+
| 53a5efd0-acc5-41ac-a683-be67559ca743 | metallic-server | ACTIVE | physnet2-network=10.27.187.66 | cirros-0.6.2-x86_64-disk | metallic-flavor |
+--------------------------------------+-----------------+--------+-------------------------------+--------------------------+-----------------+
```

## Contents

This feature will install the following services:
- Ironic: Bare metal provisioning service API for OpenStack [charm](https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/ironic-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/ironic-consolidated)
- Nova Ironic: A nova-compute service configured with the Ironic driver [charm](https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/nova-ironic-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/nova-ironic)
- Ironic Conductor: Does the bulk of the bare metal deployment work [charm](https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/ironic-conductor-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/ironic-conductor)
- Neutron baremetal switch configuration [charm](https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/neutron-baremetal-switch-config-k8s)
- Neutron generic switch configuration [charm](https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/neutron-generic-switch-config-k8s)
- MySQL Routers for Ironic [charm](https://github.com/canonical/mysql-router-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)
- MySQL Instance in the case of a multi-mysql installation (for large deployments) [charm](https://github.com/canonical/mysql-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)

Services are constituted of charms, i.e. operator code, and ROCKs, the corresponding OCI images.

## Removal

To remove the feature, run:

```bash
sunbeam disable baremetal
```
