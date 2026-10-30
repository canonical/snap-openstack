// Package access implements the access control logic for the extended apis
package access

import (
	"crypto/x509"
	"net/http"
	"strings"

	"github.com/canonical/lxd/lxd/response"
	"github.com/canonical/lxd/shared/logger"

	"github.com/canonical/microcluster/v2/rest"
	"github.com/canonical/microcluster/v2/rest/access"
	"github.com/canonical/microcluster/v2/state"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/client"
)

// AuthenticateClusterCAHandler authenticates the cluster CA for incoming requests.
// It checks if the request is trusted and verifies the client's leaf certificate against the cluster CA.
// If the request is trusted or the client certificate is successfully verified, it allows the request.
// Otherwise, it returns a forbidden response.
func AuthenticateClusterCAHandler(state state.State, r *http.Request) (bool, response.Response) {

	trusted, resp := access.AllowAuthenticated(state, r)

	if trusted {
		return trusted, resp
	}

	leader, err := state.Leader()

	if err != nil {
		logger.Errorf("Failed to get leader client: %v", err)
		return false, response.InternalError(err)
	}

	// If this takes too long, we should look into caching the cluster CA.
	clusterCA, err := client.ConfigClusterCAGet(r.Context(), leader)
	if err != nil {
		// If no CA is configured, simply reject the request
		if strings.Contains(err.Error(), "not found") {
			logger.Debug("No cluster CA configured, rejecting request")
			return false, response.Forbidden(nil)
		}
		logger.Errorf("Failed to get cluster CA: %v", err)
		return false, response.InternalError(nil)
	}

	roots := x509.NewCertPool()
	ok := roots.AppendCertsFromPEM([]byte(clusterCA))
	if !ok {
		logger.Error("Failed to parse cluster CA")
		return false, response.InternalError(nil)
	}

	if r.TLS == nil {
		logger.Error("Rejecting request without TLS")
		return false, response.Forbidden(nil)
	}

	if len(r.TLS.PeerCertificates) == 0 {
		logger.Debug("Rejecting request without peer certificates")
		return false, response.Forbidden(nil)
	}

	if len(r.TLS.PeerCertificates) > 10 {
		logger.Error("Rejecting request with too many certificates")
		return false, response.Forbidden(nil)
	}

	if verifyLeafChainsToRoot(r.TLS.PeerCertificates, roots) {
		logger.Debug("Allowing request authenticated using cluster CA")
		return true, nil
	}

	return false, response.Forbidden(nil)
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
func AuthenticateUnixHandler(_ state.State, r *http.Request) (bool, response.Response) {
	if r.RemoteAddr == "@" {
		return true, nil
	}
	return false, response.Forbidden(nil)
}

// ClusterCATrustedEndpoint is a helper to simplify the creation of a cluster peer endpoint.
func ClusterCATrustedEndpoint(handler func(state state.State, r *http.Request) response.Response, proxyTarget bool) rest.EndpointAction {
	return rest.EndpointAction{
		Handler:        handler,
		AccessHandler:  AuthenticateClusterCAHandler,
		AllowUntrusted: true,
		ProxyTarget:    proxyTarget,
	}
}
