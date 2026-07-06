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
        [ req ]
        prompt = yes
        distinguished_name = req_distinguished_name

        [ req_distinguished_name ]
        countryName = Country Name (2 letter code)
        stateOrProvinceName = State or Province Name
        organizationName = Organization Name
        commonName = Common Name

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
        x509_extensions = v3_intermediate_ca

        [ v3_root_ca ]
        basicConstraints = critical,CA:true,pathlen:2
        keyUsage = critical, keyCertSign, cRLSign

        [ v3_intermediate_ca ]
        basicConstraints = critical,CA:true,pathlen:1
        keyUsage = critical, keyCertSign, cRLSign

        [ policy_anything ]
        countryName = optional
        stateOrProvinceName = optional
        organizationName = optional
        organizationalUnitName = optional
        commonName = supplied

        EOF

.. note::
    Use a descriptive common name for the root CA certificate, such as
    `root.sunbeam.example`.

Generate the root CA private key and certificate:

   ::

        openssl genrsa -out rootca.key 8192
        openssl req -sha256 -new -x509 -days 3650 -key rootca.key \
            -config ca.conf -extensions v3_root_ca -out rootca.crt

.. note::
   During the certificate generation, you will be prompted to enter information
   such as country, state, organization, and common name.

Generate the Intermediate CA Certificate
----------------------------------------

Generate the intermediate CA private key:

   ::

        openssl genrsa -out interca1.key 8192

Create the intermediate CA CSR:

   ::

        openssl req -sha256 -new -key interca1.key -config ca.conf \
            -out interca1.csr

.. note::
   During the CSR generation, you will be prompted to enter information similar to the root CA certificate. Skip challenge password and optional company name

Sign the intermediate CSR using the root CA:

   ::

        openssl ca -batch -config ca.conf -extensions v3_intermediate_ca \
            -notext -in interca1.csr -out interca1.crt

Generate the CA chain file:

   ::

        cat interca1.crt rootca.crt > ca-chain.pem

Verify that the intermediate certificate is a valid signing CA:

   ::

        openssl verify -CAfile rootca.crt interca1.crt
        openssl x509 -in interca1.crt -noout -ext basicConstraints -ext keyUsage

The verification command should return `interca1.crt: OK`, and the key usage
should include `Certificate Sign, CRL Sign`.


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
        x509_extensions = v3_vault_ca

        [ v3_vault_ca ]
        basicConstraints = critical,CA:true,pathlen:0
        keyUsage = critical, keyCertSign, cRLSign

        [ policy_anything ]
        countryName = optional
        stateOrProvinceName = optional
        organizationName = optional
        organizationalUnitName = optional
        commonName = supplied

        EOF

.. note::
    When generating the Vault CA CSR, use the common name defined in Vault's
    config as `pki_ca_common_name`.

Sign the Vault CA CSR using the intermediate CA:

.. note::
    Ensure that you have the Vault CA CSR ready. You can generate it using the `sunbeam tls vault list_outstanding_csrs` command and save it as `vault.csr`.

   ::

        openssl ca -batch -config vault-ca.conf -extensions v3_vault_ca \
            -notext -in vault.csr -out vault.crt

Verify that the Vault CA certificate can be used as an issuer:

   ::

        openssl verify -CAfile ca-chain.pem vault.crt
        openssl x509 -in vault.crt -noout -ext basicConstraints -ext keyUsage

The verification command should return `vault.crt: OK`, and the key usage should
include `Certificate Sign, CRL Sign`.

.. note::
    The `vault.crt` file is the CA certificate that Vault will use to issue TLS certificates, and it should be provided via the `sunbeam tls vault unit_certs` command.
