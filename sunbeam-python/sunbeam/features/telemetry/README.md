# Telemetry service

This feature provides Telemetry service for Sunbeam. It is based on OpenStack Telemetry projects [Ceilometer](https://docs.openstack.org/ceilometer/latest/), [Aodh](https://docs.openstack.org/aodh/latest/), [Gnocchi](https://wiki.openstack.org/wiki/Gnocchi).

## Installation

To enable the Telemetry service, you need an already bootstraped Sunbeam instance. Then, you can install the feature with:

```bash
sunbeam enable telemetry
```

## Contents

This feature will install the following services:

- Ceilometer: Data collection service [charm](https://opendev.org/openstack/charm-ceilometer-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/ceilometer-consolidated)
- Aodh: Alarming service [charm](https://opendev.org/openstack/charm-aodh-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/aodh-consolidated)
- Gnocchi: Time series database service [charm](https://opendev.org/openstack/charm-gnocchi-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/gnocchi-consolidated)
- Ceilometer Agent: Agent on hypervisor [charm](https://opendev.org/openstack/charm-openstack-hypervisor) [SNAP](https://github.com/canonical/snap-openstack-hypervisor.git)
- TODO: ADD OPENSTACK EXPORTER INFO
- MySQL Router for Aodh [charm](https://github.com/canonical/mysql-router-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)
- MySQL Router for Gnocchi [charm](https://github.com/canonical/mysql-router-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)
- MySQL Instance in the case of a multi-mysql installation (for large deployments) [charm](https://github.com/canonical/mysql-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)

Services are constituted of charms, i.e. operator code, and ROCKs, the corresponding OCI images.

## Removal

To remove the feature, run:

```bash
sunbeam disable telemetry
```
