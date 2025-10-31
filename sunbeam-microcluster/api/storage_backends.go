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

// /1.0/storage-backend endpoint.
var storageBackendsCmd = rest.Endpoint{
	Path: "storage-backend",

	Get:  access.ClusterCATrustedEndpoint(cmdStorageBackendsGetAll, true),
	Post: access.ClusterCATrustedEndpoint(cmdStorageBackendsPost, true),
}

// /1.0/storage-backend/<backend-name> endpoint.
var storageBackendCmd = rest.Endpoint{
	Path: "storage-backend/{backendname}",

	Get:    access.ClusterCATrustedEndpoint(cmdStorageBackendGet, true),
	Delete: access.ClusterCATrustedEndpoint(cmdStorageBackendDelete, true),
	Put:    access.ClusterCATrustedEndpoint(cmdStorageBackendPut, true),
}

func cmdStorageBackendsGetAll(s state.State, r *http.Request) response.Response {

	storageBackends, err := sunbeam.ListStorageBackends(r.Context(), s)
	if err != nil {
		return response.InternalError(err)
	}

	return response.SyncResponse(true, storageBackends)
}

func cmdStorageBackendsPost(s state.State, r *http.Request) response.Response {
	var req apitypes.StorageBackend

	err := json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return response.InternalError(err)
	}

	err = sunbeam.AddStorageBackend(r.Context(), s, req.Name, req.Type, req.Principal, req.ModelUUID, req.Config)
	if err != nil {
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}

func cmdStorageBackendGet(s state.State, r *http.Request) response.Response {
	var backendName string
	backendName, err := url.PathUnescape(mux.Vars(r)["backendname"])
	if err != nil {
		return response.InternalError(err)
	}
	backend, err := sunbeam.GetStorageBackend(r.Context(), s, backendName)
	if err != nil {
		if err, ok := err.(api.StatusError); ok {
			if err.Status() == http.StatusNotFound {
				return response.NotFound(err)
			}
		}
		return response.InternalError(err)
	}

	return response.SyncResponse(true, backend)
}

func cmdStorageBackendDelete(s state.State, r *http.Request) response.Response {
	backendName, err := url.PathUnescape(mux.Vars(r)["backendname"])
	if err != nil {
		return response.SmartError(err)
	}
	err = sunbeam.DeleteStorageBackend(r.Context(), s, backendName)
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

func cmdStorageBackendPut(s state.State, r *http.Request) response.Response {
	backendName, err := url.PathUnescape(mux.Vars(r)["backendname"])
	if err != nil {
		return response.SmartError(err)
	}

	var req apitypes.StorageBackend
	err = json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return response.InternalError(err)
	}

	err = sunbeam.UpdateStorageBackend(r.Context(), s, backendName, req.Type, req.Config, req.Principal, req.ModelUUID)
	if err != nil {
		return response.InternalError(err)
	}

	return response.EmptySyncResponse
}
