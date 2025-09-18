// Package apitypes provides shared types and structs.
package apitypes

import (
	"time"
)

// Lock structure to hold terraform lock details
type Lock struct {
	ID        string    `json:"ID" yaml:"ID"`
	Operation string    `json:"Operation" yaml:"Operation"`
	Info      string    `json:"Info" yaml:"Info"`
	Who       string    `json:"Who" yaml:"Who"`
	Version   string    `json:"Version" yaml:"Version"`
	Created   time.Time `json:"Created" yaml:"Created"`
	Path      string    `json:"Path" yaml:"Path"`
}
