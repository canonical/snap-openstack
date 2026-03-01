// Package apitypes provides shared types and structs.
package apitypes

// FeatureGates holds list of FeatureGate type
type FeatureGates []FeatureGate

// FeatureGate structure to hold feature gate details
type FeatureGate struct {
	// GateKey is the snap config key (e.g., "feature.multi-region")
	GateKey string `json:"gate-key" yaml:"gate-key"`
	// Enabled indicates if the feature gate is enabled
	Enabled bool `json:"enabled" yaml:"enabled"`
}
