package api

import (
	"net/http"

	"github.com/canonical/lxd/shared/logger"
	"github.com/canonical/microcluster/v3/microcluster/types"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/access"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/api/apitypes"
)

// url path: /local/certpair/server
var certPair = types.Endpoint{
	Path: "certpair/server",

	Get: types.EndpointAction{
		Handler:       cmdGetMemberServerCertPair,
		AccessHandler: access.AuthenticateUnixHandler,
	},
}

// Return the member server certpair, should only be allowed over the Unix socket.
func cmdGetMemberServerCertPair(s types.State, _ *http.Request) types.Response {
	certs := s.ServerCert()

	if certs == nil {
		logger.Error("Failed to get server certpair")
		return types.InternalError(nil)
	}

	return types.SyncResponse(true, apitypes.CertPair{
		Certificate: string(certs.PublicKey()),
		PrivateKey:  string(certs.PrivateKey()),
	})
}
