package main

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"math/big"
	"net/http"
	"testing"
	"time"
)

// generateTestCert creates a self-signed certificate valid in the given window.
func generateTestCert(t *testing.T, notBefore, notAfter time.Time) x509.Certificate {
	t.Helper()

	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatalf("Failed to generate key: %v", err)
	}

	template := x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject:      pkix.Name{CommonName: "test-client"},
		NotBefore:    notBefore,
		NotAfter:     notAfter,
	}

	der, err := x509.CreateCertificate(rand.Reader, &template, &template, &key.PublicKey, key)
	if err != nil {
		t.Fatalf("Failed to create certificate: %v", err)
	}

	cert, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatalf("Failed to parse certificate: %v", err)
	}

	return *cert
}

func TestAuthenticateClient(t *testing.T) {
	now := time.Now()
	validCert := generateTestCert(t, now.Add(-time.Hour), now.Add(time.Hour))
	expiredCert := generateTestCert(t, now.Add(-2*time.Hour), now.Add(-time.Hour))
	futureCert := generateTestCert(t, now.Add(time.Hour), now.Add(2*time.Hour))
	otherValidCert := generateTestCert(t, now.Add(-time.Hour), now.Add(time.Hour))

	requestWithCert := func(cert *x509.Certificate) *http.Request {
		r := &http.Request{}
		r.TLS = &tls.ConnectionState{}
		if cert != nil {
			r.TLS.PeerCertificates = []*x509.Certificate{cert}
		}

		return r
	}

	trustedCerts := map[string]x509.Certificate{
		"aa:bb:cc": validCert,
	}

	testCases := []struct {
		name         string
		request      *http.Request
		trustedCerts map[string]x509.Certificate
		allowed      bool
	}{
		{
			name:         "No TLS",
			request:      &http.Request{},
			trustedCerts: trustedCerts,
			allowed:      false,
		},
		{
			name:         "No peer certificates",
			request:      requestWithCert(nil),
			trustedCerts: trustedCerts,
			allowed:      false,
		},
		{
			name:         "Expired certificate",
			request:      requestWithCert(&expiredCert),
			trustedCerts: trustedCerts,
			allowed:      false,
		},
		{
			name:         "Certificate not yet valid",
			request:      requestWithCert(&futureCert),
			trustedCerts: trustedCerts,
			allowed:      false,
		},
		{
			name:         "Valid certificate not in trusted certs",
			request:      requestWithCert(&otherValidCert),
			trustedCerts: trustedCerts,
			allowed:      false,
		},
		{
			name:         "Valid certificate in trusted certs",
			request:      requestWithCert(&validCert),
			trustedCerts: trustedCerts,
			allowed:      true,
		},
		{
			// Pin the mTLS chain-impersonation fix: only the leaf is
			// checked, so a trusted certificate appended to the chain
			// must not authenticate.
			name: "Trusted certificate appended as non-leaf",
			request: func() *http.Request {
				r := &http.Request{}
				r.TLS = &tls.ConnectionState{}
				r.TLS.PeerCertificates = []*x509.Certificate{&otherValidCert, &validCert}

				return r
			}(),
			trustedCerts: trustedCerts,
			allowed:      false,
		},
		{
			name:         "No trusted certs",
			request:      requestWithCert(&validCert),
			trustedCerts: nil,
			allowed:      false,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			allowed, err := authenticateClient(context.Background(), nil, tc.request, tc.trustedCerts)

			if err != nil {
				t.Errorf("Expected no error, got %v", err)
			}

			if allowed != tc.allowed {
				t.Errorf("Expected allowed=%v, got %v", tc.allowed, allowed)
			}
		})
	}
}
