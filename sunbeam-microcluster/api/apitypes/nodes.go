// Package apitypes provides shared types and structs.
package apitypes

// Nodes holds list of Node type
type Nodes []Node

// Node structure to hold node details like role and machine id
type Node struct {
	Name string   `json:"name" yaml:"name"`
	Role []string `json:"role" yaml:"role"`
	// MachineID is the unique identifier for the node in juju
	MachineID int `json:"machineid" yaml:"machineid"`
	// SystemID is the unique identifier for the node in machine provider
	SystemID string `json:"systemid" yaml:"systemid"`
}
