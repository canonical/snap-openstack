package api

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/url"

	"github.com/canonical/lxd/shared/api"
	"github.com/canonical/microcluster/v3/microcluster/types"
	"github.com/gorilla/mux"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/access"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/api/apitypes"
	"github.com/canonical/snap-openstack/sunbeam-microcluster/sunbeam"
)

// /1.0/jujuusers endpoint.
var jujuusersCmd = types.Endpoint{
	Path: "jujuusers",

	Get:  access.ClusterCATrustedEndpoint(cmdJujuUsersGetAll, true),
	Post: access.ClusterCATrustedEndpoint(cmdJujuUsersPost, true),
}

// /1.0/jujuusers/<name> endpoint.
var jujuuserCmd = types.Endpoint{
	Path: "jujuusers/{name}",

	Get:    access.ClusterCATrustedEndpoint(cmdJujuUsersGet, true),
	Put:    access.ClusterCATrustedEndpoint(cmdJujuUsersPut, true),
	Delete: access.ClusterCATrustedEndpoint(cmdJujuUsersDelete, true),
}

func cmdJujuUsersGetAll(s types.State, r *http.Request) types.Response {
	users, err := sunbeam.ListJujuUsers(r.Context(), s)
	if err != nil {
		return types.InternalError(err)
	}

	return types.SyncResponse(true, users)
}

func cmdJujuUsersGet(s types.State, r *http.Request) types.Response {
	var name string
	name, err := url.PathUnescape(mux.Vars(r)["name"])
	if err != nil {
		return types.InternalError(err)
	}
	jujuUser, err := sunbeam.GetJujuUser(r.Context(), s, name)
	if err != nil {
		// Return the appropriate error if juju user is not found
		var statusErr api.StatusError
		if errors.As(err, &statusErr) {
			if statusErr.Status() == http.StatusNotFound {
				return types.NotFound(err)
			}
		}
		return types.InternalError(err)
	}

	return types.SyncResponse(true, jujuUser)
}

func cmdJujuUsersPost(s types.State, r *http.Request) types.Response {
	var req apitypes.JujuUser

	err := json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return types.InternalError(err)
	}

	err = sunbeam.AddJujuUser(r.Context(), s, req.Username, req.Token)
	if err != nil {
		return types.InternalError(err)
	}

	return types.EmptySyncResponse
}

func cmdJujuUsersPut(s types.State, r *http.Request) types.Response {
	var req apitypes.JujuUser

	name, err := url.PathUnescape(mux.Vars(r)["name"])
	if err != nil {
		return types.SmartError(err)
	}

	err = json.NewDecoder(r.Body).Decode(&req)
	if err != nil {
		return types.InternalError(err)
	}

	err = sunbeam.UpdateJujuUser(r.Context(), s, name, req.Token)
	if err != nil {
		// Return the appropriate error if juju user is not found
		var statusErr api.StatusError
		if errors.As(err, &statusErr) {
			if statusErr.Status() == http.StatusNotFound {
				return types.NotFound(err)
			}
		}
		return types.InternalError(err)
	}

	return types.EmptySyncResponse
}

func cmdJujuUsersDelete(s types.State, r *http.Request) types.Response {
	name, err := url.PathUnescape(mux.Vars(r)["name"])
	if err != nil {
		return types.SmartError(err)
	}
	err = sunbeam.DeleteJujuUser(r.Context(), s, name)
	if err != nil {
		return types.InternalError(err)
	}

	return types.EmptySyncResponse
}
