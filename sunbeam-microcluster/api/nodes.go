package api

import (
	"encoding/json"
	"net/http"
	"net/url"

	"github.com/canonical/lxd/lxd/response"
	"github.com/canonical/lxd/shared/api"
	"github.com/canonical/microcluster/v2/rest"
	"github.com/canonical/microcluster/v2/state"
	"github.com/gorilla/mux"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/access"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/api/apitypes"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/sunbeam"
)

// /1.0/nodes endpoint.
var nodesCmd = rest.Endpoint{
	Path: "nodes",

	Get:  access.ClusterCATrustedEndpoint(cmdNodesGetAll, true),
	Post: access.ClusterCATrustedEndpoint(cmdNodesPost, true),
}

// /1.0/nodes/<name> endpoint.
var nodeCmd = rest.Endpoint{
	Path: "nodes/{name}",

	Get:    access.ClusterCATrustedEndpoint(cmdNodesGet, true),
	Put:    access.ClusterCATrustedEndpoint(cmdNodesPut, true),
	Delete: access.ClusterCATrustedEndpoint(cmdNodesDelete, true),
}

func cmdNodesGetAll(s state.State, r *http.Request) response.Response {
	roles := r.URL.Query()["role"]

	nodes, err := sunbeam.ListNodes(r.Context(), s, roles)
	if err != nil {
		return response.InternalError(err)
	}

	return response.SyncResponse(true, nodes)
}

func cmdNodesGet(s state.State, r *http.Request) response.Response {
	var name string
	name, err := url.PathUnescape(mux.Vars(r)["name"])
	if err != nil {
		return response.InternalError(err)
	}
	node, err := sunbeam.GetNode(r.Context(), s, name)
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

func cmdNodesPost(s state.State, r *http.Request) response.Response {
	req := apitypes.Node{MachineID: -1}

	err := json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return response.InternalError(err)
	}

	isDPU := false
	if req.IsDPU != nil {
		isDPU = *req.IsDPU
	}
	err = sunbeam.AddNode(r.Context(), s, req.Name, req.Role, req.MachineID, req.SystemID, req.Arch, isDPU, req.ImageName)
	if err != nil {
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}

func cmdNodesPut(s state.State, r *http.Request) response.Response {
	req := apitypes.Node{MachineID: -1}
	raw := map[string]json.RawMessage{}

	name, err := url.PathUnescape(mux.Vars(r)["name"])
	if err != nil {
		return response.InternalError(err)
	}

	decoder := json.NewDecoder(r.Body)
	if err := decoder.Decode(&raw); err != nil {
		return response.InternalError(err)
	}

	payload, err := json.Marshal(raw)
	if err != nil {
		return response.InternalError(err)
	}

	if err := json.Unmarshal(payload, &req); err != nil {
		return response.InternalError(err)
	}

	var imageName *string
	if _, ok := raw["image_name"]; ok {
		imageName = &req.ImageName
	}

	err = sunbeam.UpdateNode(r.Context(), s, name, req.Role, req.MachineID, req.SystemID, req.Arch, req.IsDPU, imageName)
	if err != nil {
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}

func cmdNodesDelete(s state.State, r *http.Request) response.Response {
	name, err := url.PathUnescape(mux.Vars(r)["name"])
	if err != nil {
		return response.SmartError(err)
	}
	err = sunbeam.DeleteNode(r.Context(), s, name)
	if err != nil {
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}
