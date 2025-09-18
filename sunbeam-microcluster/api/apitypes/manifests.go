// Package apitypes provides shared types and structs.
package apitypes

// Manifests holds list of manifest type
type Manifests []Manifest

// Manifest structure to hold manifest applytime and manifest data
type Manifest struct {
	ManifestID  string `json:"manifestid" yaml:"manifestid"`
	AppliedDate string `json:"applieddate" yaml:"applieddate"`
	Data        string `json:"data" yaml:"data"`
}
