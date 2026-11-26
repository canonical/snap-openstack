package api

import (
	"bytes"
	"net/http"
	"net/url"

	"github.com/canonical/lxd/shared/api"
	"github.com/canonical/microcluster/v3/microcluster/types"
	"github.com/gorilla/mux"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/access"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/sunbeam"
)

// /1.0/config/<name> endpoint.
var configCmd = types.Endpoint{
	Path: "config/{key}",

	Get:    access.ClusterCATrustedEndpoint(cmdConfigGet, true),
	Put:    access.ClusterCATrustedEndpoint(cmdConfigPut, true),
	Delete: access.ClusterCATrustedEndpoint(cmdConfigDelete, true),
}

func cmdConfigGet(s types.State, r *http.Request) types.Response {
	var key string
	key, err := url.PathUnescape(mux.Vars(r)["key"])
	if err != nil {
		return types.InternalError(err)
	}
	config, err := sunbeam.GetConfig(r.Context(), s, key)
	if err != nil {
		if err, ok := err.(api.StatusError); ok {
			if err.Status() == http.StatusNotFound {
				return types.NotFound(err)
			}
		}
		return types.InternalError(err)
	}

	return types.SyncResponse(true, config)
}

func cmdConfigPut(s types.State, r *http.Request) types.Response {
	key, err := url.PathUnescape(mux.Vars(r)["key"])
	if err != nil {
		return types.InternalError(err)
	}

	var body bytes.Buffer
	_, err = body.ReadFrom(r.Body)
	if err != nil {
		return types.InternalError(err)
	}

	err = sunbeam.UpdateConfig(r.Context(), s, key, body.String())
	if err != nil {
		return types.InternalError(err)
	}

	return types.EmptySyncResponse
}

func cmdConfigDelete(s types.State, r *http.Request) types.Response {
	key, err := url.PathUnescape(mux.Vars(r)["key"])
	if err != nil {
		return types.InternalError(err)
	}

	err = sunbeam.DeleteConfig(r.Context(), s, key)
	if err != nil {
		if err, ok := err.(api.StatusError); ok {
			if err.Status() == http.StatusNotFound {
				return types.NotFound(err)
			}
		}
		return types.InternalError(err)
	}

	return types.EmptySyncResponse
}
