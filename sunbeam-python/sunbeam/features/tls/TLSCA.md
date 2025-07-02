# TLS CA feature (Manual TLS Certificate Operator)

This feature enables user to provide TLS certificates obtained through manual process. The certificates can be applied on public and internal traefik instances.

## Installation

To enable the TLS CA feature, you need an already bootstraped Sunbeam instance. Then, you can install the feature with:

```bash
sunbeam enable tls ca --ca=<Base64 encoded CA cert> --ca-chain=<Base64 encoded CA Chain>
```

By default the TLS Certificate charm integrates with public traefik instances.
To integrate with internal traefik instances as well, install the feature with:

```bash
sunbeam enable tls ca --ca=<Base64 encoded CA cert> --ca-chain=<Base64 encoded CA Chain> --endpoint public --endpoint internal
```

To integrate with internal and rgw traefik instances as well, install the feature with:

```bash
sunbeam enable tls ca --ca=<Base64 encoded CA cert> --ca-chain=<Base64 encoded CA Chain> --endpoint public --endpoint internal --endpoint rgw
```

## Configure

To apply tls certificate on the traefik instances, run the following command:

```bash
sunbeam tls ca unit_certs
```

The above command will prompt the user for TLS Certificate for each traefik unit.

## Contents

This feature will install the following services:
- Manual TLS Certificate operator: [charm](https://github.com/canonical/manual-tls-certificates-operator)

## Removal

To remove the feature, run:

```bash
sunbeam disable tls ca
```
