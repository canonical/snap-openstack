// Package apitypes provides shared types and structs.
package apitypes

// StorageBackends holds list of StorageBackend type
type StorageBackends []StorageBackend

// StorageBackend structure to hold storage backend details like name and type
type StorageBackend struct {
	// Name of the storage backend
	Name string `json:"name" yaml:"name"`
	// Type of the storage backend
	Type string `json:"type" yaml:"type"`
	// Config holds backend specific configuration as a json blob
	Config string `json:"config" yaml:"config"`
	// Name of the principal application this storage backend is associated with
	Principal string `json:"principal" yaml:"principal"`
	// ModelUUID is the juju model UUID where this storage backend is deployed
	ModelUUID string `json:"model-uuid" yaml:"model-uuid"`
}
