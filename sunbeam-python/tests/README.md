# About

The `tests` folder contains both unit and functional tests that exercise
the Sunbeam cli.

# Functional tests

The functional tests from `tests/functional/local` run against a local
cluster deployed in manual mode. If no deployment is detected, a new one
will be bootstrapped.

## Prerequisites

### Hardware

Some of the tests (e.g. SR-IOV) require dedicated hardware that will
be specified using command arguments (e.g the SR-IOV interface to use).
This is safer than trying to discover which resources to use and it allows
exercising multiple scenarios.

### Manifest configuration

A manifest used during bootstrap (and other Sunbeam operations) may be provided.

Newly introduced features may not have been backported or promoted to the
`stable` channel yet, so make sure to specify the necessary snap and charm
channels in the manifest (e.g. `2026.1/edge`).

### Kernel parameters

Use kernel parameters to enable the following:

* 2 or more isolated CPUs
* 4 or more 1GB huge pages
* IO-MMU

Example:

```
isolcpus=0-3,16-19 default_hugepagesz=1G hugepagesz=1G hugepages=16 intel_iommu=on iommu=pt pci=realloc pci=assign-busses
```

### Snap permissions

Sunbeam uses the `microstack_support` builtin snap interface, however it can
take a while until the updates reach the `stable` channel.

In order to ensure that the snaps have the necessary privileges, consider
using the `edge` snapd channel for testing purposes.

Other snap privilege requests go through the https://forum.snapcraft.io
platform, which again can be a slow process.

If the tested snaps (e.g. `openstack-hypervisor`) do not have the necessary
privileges yet, consider using `--devmode` or rebuild the snaps with the
right privileges and add the snap plugs manually.

## Sample invocation

```
tox -e functional -- \
	--sriov-interface-name=eno2 \
	--manifest-path ~/sunbeam-manifest.yaml
```
