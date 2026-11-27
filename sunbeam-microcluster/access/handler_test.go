package access

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"crypto/x509/pkix"
	"math/big"
	"testing"
	"time"
)

// generateTestCA creates a self-signed CA certificate and its private key.
func generateTestCA(t *testing.T) (*x509.Certificate, *ecdsa.PrivateKey) {
	t.Helper()

	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatalf("Failed to generate CA key: %v", err)
	}

	template := x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "test-ca"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(time.Hour),
		IsCA:                  true,
		BasicConstraintsValid: true,
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
	}

	der, err := x509.CreateCertificate(rand.Reader, &template, &template, &key.PublicKey, key)
	if err != nil {
		t.Fatalf("Failed to create CA certificate: %v", err)
	}

	cert, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatalf("Failed to parse CA certificate: %v", err)
	}

	return cert, key
}

// generateTestCertSigned creates a leaf certificate signed by the given CA.
func generateTestCertSigned(t *testing.T, ca *x509.Certificate, caKey *ecdsa.PrivateKey, commonName string) *x509.Certificate {
	t.Helper()

	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatalf("Failed to generate leaf key: %v", err)
	}

	template := x509.Certificate{
		SerialNumber: big.NewInt(time.Now().UnixNano()),
		Subject:      pkix.Name{CommonName: commonName},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth, x509.ExtKeyUsageServerAuth},
	}

	der, err := x509.CreateCertificate(rand.Reader, &template, ca, &key.PublicKey, caKey)
	if err != nil {
		t.Fatalf("Failed to create leaf certificate: %v", err)
	}

	cert, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatalf("Failed to parse leaf certificate: %v", err)
	}

	return cert
}

// generateTestSelfSignedCert creates a self-signed leaf certificate (attacker-style).
func generateTestSelfSignedCert(t *testing.T, commonName string) *x509.Certificate {
	t.Helper()

	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatalf("Failed to generate key: %v", err)
	}

	template := x509.Certificate{
		SerialNumber: big.NewInt(2),
		Subject:      pkix.Name{CommonName: commonName},
		NotBefore:    time.Now().Add(-time.Hour),
		NotAfter:     time.Now().Add(time.Hour),
		KeyUsage:     x509.KeyUsageDigitalSignature,
		ExtKeyUsage:  []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth, x509.ExtKeyUsageServerAuth},
	}

	der, err := x509.CreateCertificate(rand.Reader, &template, &template, &key.PublicKey, key)
	if err != nil {
		t.Fatalf("Failed to create self-signed certificate: %v", err)
	}

	cert, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatalf("Failed to parse self-signed certificate: %v", err)
	}

	return cert
}

// generateTestIntermediate creates an intermediate CA certificate signed by the given root CA.
func generateTestIntermediate(t *testing.T, ca *x509.Certificate, caKey *ecdsa.PrivateKey) (*x509.Certificate, *ecdsa.PrivateKey) {
	t.Helper()

	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatalf("Failed to generate intermediate key: %v", err)
	}

	template := x509.Certificate{
		SerialNumber:          big.NewInt(3),
		Subject:               pkix.Name{CommonName: "test-intermediate"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(time.Hour),
		IsCA:                  true,
		BasicConstraintsValid: true,
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
	}

	der, err := x509.CreateCertificate(rand.Reader, &template, ca, &key.PublicKey, caKey)
	if err != nil {
		t.Fatalf("Failed to create intermediate certificate: %v", err)
	}

	cert, err := x509.ParseCertificate(der)
	if err != nil {
		t.Fatalf("Failed to parse intermediate certificate: %v", err)
	}

	return cert, key
}

func TestVerifyLeafChainsToRoot(t *testing.T) {
	ca, caKey := generateTestCA(t)
	caSignedLeaf := generateTestCertSigned(t, ca, caKey, "ca-signed-client")
	otherCASelfSigned := generateTestSelfSignedCert(t, "attacker")

	roots := x509.NewCertPool()
	roots.AddCert(ca)

	testCases := []struct {
		name      string
		peerCerts []*x509.Certificate
		allowed   bool
	}{
		{
			name:      "Leaf signed by cluster CA",
			peerCerts: []*x509.Certificate{caSignedLeaf},
			allowed:   true,
		},
		{
			name:      "Self-signed leaf",
			peerCerts: []*x509.Certificate{otherCASelfSigned},
			allowed:   false,
		},
		{
			name:      "Self-signed leaf with CA-signed certificate appended to chain",
			peerCerts: []*x509.Certificate{otherCASelfSigned, caSignedLeaf},
			allowed:   false,
		},
		{
			name:      "No peer certificates",
			peerCerts: nil,
			allowed:   false,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			allowed := verifyLeafChainsToRoot(tc.peerCerts, roots)

			if allowed != tc.allowed {
				t.Errorf("Expected allowed=%v, got %v", tc.allowed, allowed)
			}
		})
	}
}

// TestVerifyLeafChainsToRootWithIntermediates pins that the rest of the presented
// chain is usable as intermediates for honest clients, without letting appended
// non-leaf certificates authenticate on their own.
func TestVerifyLeafChainsToRootWithIntermediates(t *testing.T) {
	ca, caKey := generateTestCA(t)
	intermediate, intermediateKey := generateTestIntermediate(t, ca, caKey)
	intermediateSignedLeaf := generateTestCertSigned(t, intermediate, intermediateKey, "intermediate-signed-client")
	unrelatedCert := generateTestSelfSignedCert(t, "unrelated")

	roots := x509.NewCertPool()
	roots.AddCert(ca)

	testCases := []struct {
		name      string
		peerCerts []*x509.Certificate
		allowed   bool
	}{
		{
			name:      "Leaf signed by intermediate, intermediate presented in chain",
			peerCerts: []*x509.Certificate{intermediateSignedLeaf, intermediate},
			allowed:   true,
		},
		{
			name:      "Leaf signed by intermediate, intermediate NOT presented",
			peerCerts: []*x509.Certificate{intermediateSignedLeaf},
			allowed:   false,
		},
		{
			name:      "CA-signed leaf with unrelated certificate appended after it",
			peerCerts: []*x509.Certificate{generateTestCertSigned(t, ca, caKey, "ca-signed-client"), unrelatedCert},
			allowed:   true,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			allowed := verifyLeafChainsToRoot(tc.peerCerts, roots)

			if allowed != tc.allowed {
				t.Errorf("Expected allowed=%v, got %v", tc.allowed, allowed)
			}
		})
	}
}
