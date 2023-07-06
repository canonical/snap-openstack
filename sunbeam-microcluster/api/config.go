package api

import (
	"bytes"
	"net/http"
	"net/url"

	"github.com/canonical/lxd/lxd/response"
	"github.com/canonical/lxd/shared/api"
	"github.com/canonical/microcluster/rest"
	"github.com/canonical/microcluster/state"
	"github.com/gorilla/mux"

	"github.com/openstack-snaps/snap-openstack/sunbeam-microcluster/sunbeam"
)

// /1.0/config/<name> endpoint.
var configCmd = rest.Endpoint{
	Path: "config/{key}",

	Get:    rest.EndpointAction{Handler: cmdConfigGet, ProxyTarget: true},
	Put:    rest.EndpointAction{Handler: cmdConfigPut, ProxyTarget: true},
	Delete: rest.EndpointAction{Handler: cmdConfigDelete, ProxyTarget: true},
}

func cmdConfigGet(s *state.State, r *http.Request) response.Response {
	var key string
	key, err := url.PathUnescape(mux.Vars(r)["key"])
	if err != nil {
		return response.InternalError(err)
	}
	config, err := sunbeam.GetConfig(s, key)
	if err != nil {
		if err, ok := err.(api.StatusError); ok {
			if err.Status() == http.StatusNotFound {
				return response.NotFound(err)
			}
		}
		return response.InternalError(err)
	}

	return response.SyncResponse(true, config)
}

func cmdConfigPut(s *state.State, r *http.Request) response.Response {
	key, err := url.PathUnescape(mux.Vars(r)["key"])
	if err != nil {
		return response.InternalError(err)
	}

	var body bytes.Buffer
	_, err = body.ReadFrom(r.Body)
	if err != nil {
		return response.InternalError(err)
	}

	err = sunbeam.UpdateConfig(s, key, body.String())
	if err != nil {
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}

func cmdConfigDelete(s *state.State, r *http.Request) response.Response {
	key, err := url.PathUnescape(mux.Vars(r)["key"])
	if err != nil {
		return response.InternalError(err)
	}

	err = sunbeam.DeleteConfig(s, key)
	if err != nil {
		if err, ok := err.(api.StatusError); ok {
			if err.Status() == http.StatusNotFound {
				return response.NotFound(err)
			}
		}
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}
