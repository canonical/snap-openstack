Generating a CA Certificate for TLS Vault
=========================================

This guide explains how to generate a CA certificate that can be used to enable Vault to issue TLS certificates for your Canonical OpenStack cloud.

.. note::
   This guide will show how to generate the root CA certificate and the intermediate CA certificate. The root CA certificate is used to sign the intermediate CA certificate, which in turn is used to sign the TLS certificates for Vault.

Generate the Root CA Certificate
--------------------------------

Create a directory to store the CA files:

   ::

        mkdir -p ~/ca
        cd ~/ca

Create the index files and serial number file:

   ::

        touch certindex
        echo 1000 > certserial
        echo 1000 > crlnumber

Create the following configuration file to define the CA settings. Save it as `ca.conf`:

   ::

        cat <<EOF > ca.conf
        [ ca ]
        default_ca = CA_default

        [ CA_default ]
        dir = .
        database = certindex
        new_certs_dir = .
        certificate = rootca.crt
        private_key = rootca.key
        serial = certserial
        # Defaults for issuing
        default_days      = 375
        default_crl_days  =  30
        default_md        = sha256
        policy = policy_anything
        x509_extensions = v3_ca

        [ v3_ca ]
        basicConstraints = critical,CA:true

        [ policy_anything ]
        countryName = optional
        stateOrProvinceName = optional
        organizationName = optional
        organizationalUnitName = optional
        commonName = supplied

        [alt_names]
        DNS.1 = <commonName>

        EOF

.. note::
    Replace `<commonName>` in the `alt_names` section with the common name you want to use for the root CA certificate.

Generate the root CA private key and certificate:

   ::

        openssl genrsa -out rootca.key 8192
        openssl req -sha256 -new -x509 -days 3650 -key rootca.key -out rootca.crt

.. note::
   During the certificate generation, you will be prompted to enter information such as country, state, organization, and common name. Ensure that the common name matches the one specified in the `alt_names` section of the configuration file.

Generate the Intermediate CA Certificate
----------------------------------------

Generate the intermediate CA private key:

   ::

        openssl genrsa -out interca1.key 8192

Create the intermediate CA CSR:

   ::

        openssl req -sha256 -new -key interca1.key -out interca1.csr

.. note::
   During the CSR generation, you will be prompted to enter information similar to the root CA certificate. Skip challenge password and optional company name

Sign the intermediate CSR using the root CA:

   ::

        openssl ca -batch -config ca.conf -notext -in interca1.csr -out interca1.crt

Generate the CA chain file:

   ::

        cat rootca.crt interca1.crt > ca-chain.pem


Generate the CA required for Vault
----------------------------------

To generate the CA required for Vault, a new CA configuration file is needed. Create a new configuration file named `vault-ca.conf`:

   ::

        cat <<EOF > vault-ca.conf
        [ ca ]
        default_ca = CA_default

        [ CA_default ]
        dir = .
        database = certindex
        new_certs_dir = .
        certificate = interca1.crt
        private_key = interca1.key
        serial = certserial
        # Defaults for issuing
        default_days      = 375
        default_crl_days  =  30
        default_md        = sha256
        policy = policy_anything
        x509_extensions = v3_ca

        [ v3_ca ]
        basicConstraints = critical,CA:true

        [ policy_anything ]
        countryName = optional
        stateOrProvinceName = optional
        organizationName = optional
        organizationalUnitName = optional
        commonName = supplied

        [alt_names]
        DNS.1 = <commonName>

        EOF

.. note::
    Replace `<commonName>` in the `alt_names` section with the common name defined in Vault' config as `common_name`.

Sign the Vault CA CSR using the intermediate CA:

.. note::
    Ensure that you have the Vault CA CSR ready. You can generate it using the `sunbeam tls vault list_outstanding_csrs` command and save it as `vault.csr`.

   ::

        openssl ca -batch -config vault-ca.conf -notext -in vault.csr -out vault.crt

.. note::
    The `vault.crt` file is the CA certificate that Vault will use to issue TLS certificates, and it should be provided via the `sunbeam tls vault unit_certs` command.