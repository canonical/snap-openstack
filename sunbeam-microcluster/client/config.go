// Package client contains helper functions to configure the microcluster
package client

import (
	"context"
	"time"

	"github.com/canonical/lxd/shared/api"
	"github.com/canonical/microcluster/v3/microcluster/types"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/api/apitypes"
)

const (
	// ClusterCA is the key for the cluster CA configuration.
	ClusterCA = "cluster-ca"
)

// ConfigClusterCASet configures the cluster ca.
// This CA is used to validate incoming queries to extended endpoints.
func ConfigClusterCASet(ctx context.Context, c types.Client, data string) error {
	queryCtx, cancel := context.WithTimeout(ctx, time.Second*60)
	defer cancel()

	err := c.Query(queryCtx, "PUT", apitypes.ExtendedPathPrefix, &api.NewURL().Path("config", ClusterCA).URL, data, nil)
	if err != nil {
		return err
	}

	return nil
}

// ConfigClusterCAGet fetches the cluster ca.
func ConfigClusterCAGet(ctx context.Context, c types.Client) (string, error) {
	queryCtx, cancel := context.WithTimeout(ctx, time.Second*60)
	defer cancel()

	var data string
	err := c.Query(queryCtx, "GET", apitypes.ExtendedPathPrefix, &api.NewURL().Path("config", ClusterCA).URL, nil, &data)
	if err != nil {
		return "", err
	}

	return data, nil
}
