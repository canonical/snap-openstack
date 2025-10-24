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
