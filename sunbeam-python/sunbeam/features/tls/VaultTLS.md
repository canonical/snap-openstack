# TLS Vault feature

This feature enables user to provide TLS certificates obtained through manual process. The certificates can be applied on public and internal traefik instances.

## Prerequisites

This feature requires the Vault feature enabled in sunbeam. For this, follow this [guide](https://canonical-openstack.readthedocs-hosted.com/en/latest/how-to/features/vault/).

## Installation

To enable the TLS Vault feature, you need an already bootstraped Sunbeam instance. Then, you can install the feature with:

```bash
sunbeam enable tls vault --ca=<Base64 encoded CA cert> --ca-chain=<Base64 encoded CA Chain>
```

**NOTE**: The certificate needs to be created as a CA certificate. This can be done by adding to the certificate the `x509 Extension -> CA:TRUE`.

By default the TLS Certificate charm integrates with public traefik instances.
To integrate with internal traefik instances as well, install the feature with:

```bash
sunbeam enable tls vault --ca=<Base64 encoded CA cert> --ca-chain=<Base64 encoded CA Chain> --endpoint public --endpoint internal
```

To integrate with internal and rgw traefik instances as well, install the feature with:

```bash
sunbeam enable tls vault --ca=<Base64 encoded CA cert> --ca-chain=<Base64 encoded CA Chain> --endpoint public --endpoint internal --endpoint rgw
```

## Configure

To apply tls certificate on the traefik instances, run the following command:

```bash
sunbeam tls vault unit_certs
```

The above command will prompt the user for TLS Certificate for each traefik unit.

## Contents

This feature will require the following services:
- Vault-K8s: [charm](https://github.com/canonical/vault-k8s-operator)

## Removal

To remove the feature, run:

```bash
sunbeam disable tls vault
```
