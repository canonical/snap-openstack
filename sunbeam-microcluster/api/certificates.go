package api

import (
	"net/http"

	"github.com/canonical/lxd/lxd/response"
	"github.com/canonical/lxd/shared/logger"
	"github.com/canonical/microcluster/v2/rest"
	"github.com/canonical/microcluster/v2/state"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/access"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/api/apitypes"
)

// url path: /local/certpair/server
var certPair = rest.Endpoint{
	Path: "certpair/server",

	Get: rest.EndpointAction{
		Handler:       cmdGetMemberServerCertPair,
		AccessHandler: access.AuthenticateUnixHandler,
	},
}

// Return the member server certpair, should only be allowed over the Unix socket.
func cmdGetMemberServerCertPair(s state.State, _ *http.Request) response.Response {
	certs := s.ServerCert()

	if certs == nil {
		logger.Error("Failed to get server certpair")
		return response.InternalError(nil)
	}

	return response.SyncResponse(true, apitypes.CertPair{
		Certificate: string(certs.PublicKey()),
		PrivateKey:  string(certs.PrivateKey()),
	})
}
