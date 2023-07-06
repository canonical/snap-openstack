package api

import (
	"encoding/json"
	"net/http"
	"net/url"

	"github.com/canonical/lxd/lxd/response"
	"github.com/canonical/lxd/shared/api"
	"github.com/canonical/microcluster/rest"
	"github.com/canonical/microcluster/state"
	"github.com/gorilla/mux"

	"github.com/openstack-snaps/snap-openstack/sunbeam-microcluster/api/types"
	"github.com/openstack-snaps/snap-openstack/sunbeam-microcluster/sunbeam"
)

// /1.0/nodes endpoint.
var nodesCmd = rest.Endpoint{
	Path: "nodes",

	Get:  rest.EndpointAction{Handler: cmdNodesGetAll, ProxyTarget: true},
	Post: rest.EndpointAction{Handler: cmdNodesPost, ProxyTarget: true},
}

// /1.0/nodes/<name> endpoint.
var nodeCmd = rest.Endpoint{
	Path: "nodes/{name}",

	Get:    rest.EndpointAction{Handler: cmdNodesGet, ProxyTarget: true},
	Put:    rest.EndpointAction{Handler: cmdNodesPut, ProxyTarget: true},
	Delete: rest.EndpointAction{Handler: cmdNodesDelete, ProxyTarget: true},
}

func cmdNodesGetAll(s *state.State, r *http.Request) response.Response {
	roles := r.URL.Query()["role"]

	nodes, err := sunbeam.ListNodes(s, roles)
	if err != nil {
		return response.InternalError(err)
	}

	return response.SyncResponse(true, nodes)
}

func cmdNodesGet(s *state.State, r *http.Request) response.Response {
	var name string
	name, err := url.PathUnescape(mux.Vars(r)["name"])
	if err != nil {
		return response.InternalError(err)
	}
	node, err := sunbeam.GetNode(s, name)
	if err != nil {
		if err, ok := err.(api.StatusError); ok {
			if err.Status() == http.StatusNotFound {
				return response.NotFound(err)
			}
		}
		return response.InternalError(err)
	}

	return response.SyncResponse(true, node)
}

func cmdNodesPost(s *state.State, r *http.Request) response.Response {
	var req types.Node

	err := json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return response.InternalError(err)
	}

	err = sunbeam.AddNode(s, req.Name, req.Role, req.MachineID)
	if err != nil {
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}

func cmdNodesPut(s *state.State, r *http.Request) response.Response {
	var req types.Node

	name, err := url.PathUnescape(mux.Vars(r)["name"])
	if err != nil {
		return response.InternalError(err)
	}

	err = json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return response.InternalError(err)
	}

	err = sunbeam.UpdateNode(s, name, req.Role, req.MachineID)
	if err != nil {
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}

func cmdNodesDelete(s *state.State, r *http.Request) response.Response {
	name, err := url.PathUnescape(mux.Vars(r)["name"])
	if err != nil {
		return response.SmartError(err)
	}
	err = sunbeam.DeleteNode(s, name)
	if err != nil {
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}
