# DNS service

This feature provides a DNS service for Sunbeam. It's based on [Designate](https://docs.openstack.org/designate/latest/), a DNS service for OpenStack and Bind.

## Installation

To enable the DNS service, you need an already bootstraped Sunbeam instance. Then, you can install the feature with:

```bash
sunbeam enable dns "<ns records>"
```

## Contents

This feature will install the following services:
- Bind: a DNS server [charm](https://opendev.org/openstack/charm-bind-k8s) [ROCK](https://git.launchpad.net/~ubuntu-docker-images/ubuntu-docker-images/+git/bind9)
- Designate: a DNS service for OpenStack [charm](https://opendev.org/openstack/charm-designate-k8s) [ROCK](https://github.com/canonical/ubuntu-openstack-rocks/tree/main/rocks/designate-consolidated)
- MySQL Router for Designate [charm](https://github.com/canonical/mysql-router-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)
- MySQL Instance in the case of a multi-mysql installation (for large deployments) [charm](https://github.com/canonical/mysql-k8s-operator) [ROCK](https://github.com/canonical/charmed-mysql-rock)

Services are constituted of charms, i.e. operator code, and ROCKs, the corresponding OCI images.

## Configuration

The NS records you pass to the `nameservers` must be a fully qualified domain name ending with a dot.
It must redirect towards the IP address of the bind instance. See [#commands](#commands) to retrieve the address of the bind instance.

## Commands

To retrieve the feature commands, run:

```bash
sunbeam dns --help
```

The feature provides address command, that will return the address of the bind instance. To retrieve the address, run:

```bash
sunbeam dns address
```

## Removal

To remove the feature, run:

```bash
sunbeam disable dns
```
