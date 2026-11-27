// Package access implements the access control logic for the extended apis
package access

import (
	"context"
	"crypto/subtle"
	"crypto/x509"
	"net/http"
	"strings"
	"time"

	"github.com/canonical/lxd/shared/logger"

	"github.com/canonical/microcluster/v3/microcluster/types"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/client"
)

// AuthenticateClusterCAHandler authenticates the cluster CA for incoming requests.
// It checks if the request is trusted and verifies the client's leaf certificate against the cluster CA.
// If the request is trusted or the client certificate is successfully verified, it allows the request.
// Otherwise, it returns a forbidden response.
func AuthenticateClusterCAHandler(state types.State, r *http.Request) (bool, types.Response) {
	if r.RemoteAddr == "@" {
		return true, nil
	}

	if r.TLS == nil || len(r.TLS.PeerCertificates) == 0 {
		return false, types.Forbidden(nil)
	}

	if trustedClient(r, state.Truststore().RemoteCertificatesNative()) {
		return true, nil
	}

	if len(r.TLS.PeerCertificates) > 10 {
		logger.Error("Rejecting request with too many certificates")
		return false, types.Forbidden(nil)
	}

	leader, err := state.Connect().Leader(false)

	if err != nil {
		logger.Errorf("Failed to get leader client: %v", err)
		return false, types.InternalError(err)
	}

	// If this takes too long, we should look into caching the cluster CA.
	clusterCA, err := client.ConfigClusterCAGet(r.Context(), leader)
	if err != nil {
		// If no CA is configured, simply reject the request
		if strings.Contains(err.Error(), "not found") {
			logger.Debug("No cluster CA configured, rejecting request")
			return false, types.Forbidden(nil)
		}
		logger.Errorf("Failed to get cluster CA: %v", err)
		return false, types.InternalError(nil)
	}

	roots := x509.NewCertPool()
	ok := roots.AppendCertsFromPEM([]byte(clusterCA))
	if !ok {
		logger.Error("Failed to parse cluster CA")
		return false, types.InternalError(nil)
	}

	if verifyLeafChainsToRoot(r.TLS.PeerCertificates, roots) {
		logger.Debug("Allowing request authenticated using cluster CA")
		return true, nil
	}

	return false, types.Forbidden(nil)
}

// verifyLeafChainsToRoot reports whether the peer's leaf certificate chains to the given root pool.
// Only the leaf is verified: the TLS handshake proves possession of the leaf's private key alone,
// so verifying non-leaf certificates would let an attacker authenticate by appending any public
// CA-signed certificate to its presented chain.
func verifyLeafChainsToRoot(peerCerts []*x509.Certificate, roots *x509.CertPool) bool {
	if len(peerCerts) == 0 {
		return false
	}

	intermediates := x509.NewCertPool()
	for _, cert := range peerCerts[1:] {
		intermediates.AddCert(cert)
	}

	_, err := peerCerts[0].Verify(x509.VerifyOptions{
		Roots:         roots,
		Intermediates: intermediates,
		KeyUsages:     []x509.ExtKeyUsage{x509.ExtKeyUsageClientAuth, x509.ExtKeyUsageServerAuth},
	})

	return err == nil
}

// AuthenticateUnixHandler only allow requests coming from the unix socket.
func AuthenticateUnixHandler(_ types.State, r *http.Request) (bool, types.Response) {
	if r.RemoteAddr == "@" {
		return true, nil
	}
	return false, types.Forbidden(nil)
}

// ClusterCATrustedEndpoint is a helper to simplify the creation of a cluster peer endpoint.
func ClusterCATrustedEndpoint(handler func(state types.State, r *http.Request) types.Response, proxyTarget bool) types.EndpointAction {
	return types.EndpointAction{
		Handler:        handler,
		AccessHandler:  AuthenticateClusterCAHandler,
		AllowUntrusted: true,
		ProxyTarget:    proxyTarget,
	}
}

// AuthenticateClient matches the TLS leaf against the MicroCluster truststore.
// A nil error lets extended endpoints check their cluster CA when there is no match.
func AuthenticateClient(_ context.Context, _ types.State, r *http.Request, trustedCerts map[string]x509.Certificate) (bool, error) {
	return trustedClient(r, trustedCerts), nil
}

func trustedClient(r *http.Request, trustedCerts map[string]x509.Certificate) bool {
	if r.TLS == nil {
		return false
	}

	if len(r.TLS.PeerCertificates) == 0 {
		return false
	}

	peerCertificate := r.TLS.PeerCertificates[0]

	now := time.Now()
	if now.Before(peerCertificate.NotBefore) {
		logger.Debugf("Rejecting request from %q: certificate is not yet valid", r.RemoteAddr)

		return false
	}

	if now.After(peerCertificate.NotAfter) {
		logger.Debugf("Rejecting request from %q: certificate has expired", r.RemoteAddr)

		return false
	}

	// Check whether client certificate is in the map of trusted certs.
	for fingerprint, v := range trustedCerts {
		if subtle.ConstantTimeCompare(peerCertificate.Raw, v.Raw) == 1 {
			logger.Debugf("Matched trusted cert with fingerprint %q", fingerprint)

			return true
		}
	}

	return false
}
