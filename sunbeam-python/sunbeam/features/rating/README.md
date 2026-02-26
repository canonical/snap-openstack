# Cloudkitty service

This feature provides Rating (billing) service for Sunbeam. It's based on [Cloudkitty](https://docs.openstack.org/cloudkitty/latest/), rating solution for OpenStack.

## Installation

To enable the Rating service, you need an already bootstraped Sunbeam instance. Then, you can install the feature with:

```bash
sunbeam enable rating
```

## Contents

This feature will install the following services:
- Cloudkitty: Rating service for OpenStack [charm](https://opendev.org/openstack/sunbeam-charms/src/branch/main/charms/cloudkitty-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/cloudkitty-consolidated)
- MySQL Router for Cloudkitty [charm](https://github.com/canonical/mysql-router-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)
- MySQL Instance in the case of a multi-mysql installation (for large deployments) [charm](https://github.com/canonical/mysql-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)

Services are constituted of charms, i.e. operator code, and ROCKs, the corresponding OCI images.

The Cloudkitty charm currently supports Storage V1, Gnocchi and Ceilometer telemetry with other options to be enhanced at a later date.

## Removal

To remove the feature, run:

```bash
sunbeam disable rating
```
