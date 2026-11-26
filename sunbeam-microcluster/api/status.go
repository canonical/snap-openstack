// Package api provides the REST API endpoints.
package api

import (
	"context"
	"net/http"
	"time"

	"github.com/canonical/microcluster/v3/microcluster/types"

	"github.com/canonical/lxd/shared/api"
	"github.com/canonical/lxd/shared/logger"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/access"
)

var statusCmd = types.Endpoint{
	Path: "status",

	Get: access.ClusterCATrustedEndpoint(cmdGetStatus, false),
}

func cmdGetStatus(s types.State, r *http.Request) types.Response {
	leader, err := s.Connect().Leader(false)

	if err != nil {
		logger.Errorf("Failed to get leader client: %v", err)
		return types.InternalError(err)
	}

	queryCtx, cancel := context.WithTimeout(r.Context(), time.Second*30)
	defer cancel()

	var data []map[string]interface{}
	err = leader.Query(queryCtx, http.MethodGet, types.PublicEndpoint, &api.NewURL().Path("cluster").URL, nil, &data)
	if err != nil {
		logger.Errorf("Failed to get cluster status: %v", err)
		return types.InternalError(err)
	}

	return types.SyncResponse(true, data)
}
