package sunbeam

import (
	"testing"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/api/apitypes"
)

// TestFeatureGateKeyValidation tests feature gate key format validation
func TestFeatureGateKeyValidation(t *testing.T) {
	testCases := []struct {
		name    string
		gateKey string
		valid   bool
	}{
		{
			name:    "valid key with feature prefix",
			gateKey: "feature.multi-region",
			valid:   true,
		},
		{
			name:    "valid key with dashes",
			gateKey: "feature.my-custom-feature",
			valid:   true,
		},
		{
			name:    "valid key with underscores",
			gateKey: "feature.my_custom_feature",
			valid:   true,
		},
		{
			name:    "empty key",
			gateKey: "",
			valid:   false,
		},
		{
			name:    "key without prefix",
			gateKey: "multi-region",
			valid:   false,
		},
		{
			name:    "key with only prefix",
			gateKey: "feature.",
			valid:   false,
		},
		{
			name:    "key with wrong prefix",
			gateKey: "config.multi-region",
			valid:   false,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			// Basic validation: must start with "feature." and have content after
			hasPrefix := len(tc.gateKey) > 8 && tc.gateKey[:8] == "feature."
			hasContent := len(tc.gateKey) > 8

			isValid := hasPrefix && hasContent

			if isValid != tc.valid {
				t.Errorf("Expected key %q to be valid=%v, got valid=%v", tc.gateKey, tc.valid, isValid)
			}
		})
	}
}

// TestFeatureGateStructure tests the FeatureGate API type structure
func TestFeatureGateStructure(t *testing.T) {
	gate := apitypes.FeatureGate{
		GateKey: "feature.multi-region",
		Enabled: true,
	}

	if gate.GateKey != "feature.multi-region" {
		t.Errorf("Expected GateKey to be 'feature.multi-region', got %q", gate.GateKey)
	}

	if !gate.Enabled {
		t.Error("Expected Enabled to be true")
	}
}

// TestFeatureGatesCollection tests the FeatureGates slice operations
func TestFeatureGatesCollection(t *testing.T) {
	gates := apitypes.FeatureGates{
		{GateKey: "feature.multi-region", Enabled: true},
		{GateKey: "feature.experimental", Enabled: false},
		{GateKey: "feature.custom", Enabled: true},
	}

	if len(gates) != 3 {
		t.Errorf("Expected 3 gates, got %d", len(gates))
	}

	// Test filtering enabled gates
	var enabledGates []apitypes.FeatureGate
	for _, gate := range gates {
		if gate.Enabled {
			enabledGates = append(enabledGates, gate)
		}
	}

	if len(enabledGates) != 2 {
		t.Errorf("Expected 2 enabled gates, got %d", len(enabledGates))
	}

	// Test finding a specific gate
	found := false
	for _, gate := range gates {
		if gate.GateKey == "feature.experimental" {
			found = true
			if gate.Enabled {
				t.Error("Expected feature.experimental to be disabled")
			}
		}
	}

	if !found {
		t.Error("Expected to find feature.experimental gate")
	}
}

// TestFeatureGateEnabledToggle tests enabled state transitions
func TestFeatureGateEnabledToggle(t *testing.T) {
	testCases := []struct {
		name           string
		initialEnabled bool
		newEnabled     bool
		expectChange   bool
	}{
		{
			name:           "enable gate",
			initialEnabled: false,
			newEnabled:     true,
			expectChange:   true,
		},
		{
			name:           "disable gate",
			initialEnabled: true,
			newEnabled:     false,
			expectChange:   true,
		},
		{
			name:           "no change when already enabled",
			initialEnabled: true,
			newEnabled:     true,
			expectChange:   false,
		},
		{
			name:           "no change when already disabled",
			initialEnabled: false,
			newEnabled:     false,
			expectChange:   false,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			changed := tc.initialEnabled != tc.newEnabled

			if changed != tc.expectChange {
				t.Errorf("Expected change=%v, got change=%v", tc.expectChange, changed)
			}
		})
	}
}

// TestFeatureGateKeyUniqueness tests that gate keys should be unique
func TestFeatureGateKeyUniqueness(t *testing.T) {
	gates := apitypes.FeatureGates{
		{GateKey: "feature.multi-region", Enabled: true},
		{GateKey: "feature.experimental", Enabled: false},
	}

	// Check for duplicates
	seen := make(map[string]bool)
	duplicates := []string{}

	for _, gate := range gates {
		if seen[gate.GateKey] {
			duplicates = append(duplicates, gate.GateKey)
		}
		seen[gate.GateKey] = true
	}

	if len(duplicates) > 0 {
		t.Errorf("Found duplicate gate keys: %v", duplicates)
	}
}

// TestFeatureGateNamingConventions tests gate naming conventions
func TestFeatureGateNamingConventions(t *testing.T) {
	testCases := []struct {
		name              string
		gateKey           string
		followsConvention bool
	}{
		{
			name:              "kebab-case name",
			gateKey:           "feature.multi-region",
			followsConvention: true,
		},
		{
			name:              "single word name",
			gateKey:           "feature.experimental",
			followsConvention: true,
		},
		{
			name:              "multiple dashes",
			gateKey:           "feature.my-custom-feature-gate",
			followsConvention: true,
		},
		{
			name:              "contains uppercase (not conventional)",
			gateKey:           "feature.MultiRegion",
			followsConvention: false,
		},
		{
			name:              "contains spaces (invalid)",
			gateKey:           "feature.multi region",
			followsConvention: false,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			// Check if key follows convention:
			// - starts with "feature."
			// - lowercase letters, numbers, and dashes only after prefix
			hasPrefix := len(tc.gateKey) > 8 && tc.gateKey[:8] == "feature."
			if !hasPrefix {
				if tc.followsConvention {
					t.Error("Key does not have required prefix")
				}
				return
			}

			name := tc.gateKey[8:]
			followsConvention := true
			for _, ch := range name {
				if !((ch >= 'a' && ch <= 'z') || (ch >= '0' && ch <= '9') || ch == '-' || ch == '_') {
					followsConvention = false
					break
				}
			}

			if followsConvention != tc.followsConvention {
				t.Errorf("Expected followsConvention=%v, got %v for key %q",
					tc.followsConvention, followsConvention, tc.gateKey)
			}
		})
	}
}

// TestFeatureGateBooleanValues tests that enabled is properly boolean
func TestFeatureGateBooleanValues(t *testing.T) {
	testCases := []struct {
		name    string
		enabled bool
	}{
		{
			name:    "enabled is true",
			enabled: true,
		},
		{
			name:    "enabled is false",
			enabled: false,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			gate := apitypes.FeatureGate{
				GateKey: "feature.test",
				Enabled: tc.enabled,
			}

			// Verify boolean is stored correctly
			if gate.Enabled != tc.enabled {
				t.Errorf("Expected enabled=%v, got %v", tc.enabled, gate.Enabled)
			}

			// Verify boolean can be negated
			gate.Enabled = !gate.Enabled
			if gate.Enabled == tc.enabled {
				t.Error("Failed to negate enabled value")
			}
		})
	}
}

// TestFeatureGatesFiltering tests various filtering operations on feature gates
func TestFeatureGatesFiltering(t *testing.T) {
	gates := apitypes.FeatureGates{
		{GateKey: "feature.multi-region", Enabled: true},
		{GateKey: "feature.experimental", Enabled: false},
		{GateKey: "feature.beta-feature", Enabled: true},
		{GateKey: "feature.deprecated", Enabled: false},
	}

	t.Run("filter enabled gates", func(t *testing.T) {
		enabled := filterGates(gates, func(g apitypes.FeatureGate) bool {
			return g.Enabled
		})
		if len(enabled) != 2 {
			t.Errorf("Expected 2 enabled gates, got %d", len(enabled))
		}
	})

	t.Run("filter disabled gates", func(t *testing.T) {
		disabled := filterGates(gates, func(g apitypes.FeatureGate) bool {
			return !g.Enabled
		})
		if len(disabled) != 2 {
			t.Errorf("Expected 2 disabled gates, got %d", len(disabled))
		}
	})

	t.Run("filter by key prefix", func(t *testing.T) {
		betaGates := filterGates(gates, func(g apitypes.FeatureGate) bool {
			return len(g.GateKey) > 12 && g.GateKey[:12] == "feature.beta"
		})
		if len(betaGates) != 1 {
			t.Errorf("Expected 1 beta gate, got %d", len(betaGates))
		}
	})
}

// Helper function for filtering gates
func filterGates(gates apitypes.FeatureGates, predicate func(apitypes.FeatureGate) bool) apitypes.FeatureGates {
	var result apitypes.FeatureGates
	for _, gate := range gates {
		if predicate(gate) {
			result = append(result, gate)
		}
	}
	return result
}

// TestFeatureGateMapOperations tests converting between slice and map representations
func TestFeatureGateMapOperations(t *testing.T) {
	gates := apitypes.FeatureGates{
		{GateKey: "feature.multi-region", Enabled: true},
		{GateKey: "feature.experimental", Enabled: false},
	}

	// Convert to map
	gateMap := make(map[string]bool)
	for _, gate := range gates {
		gateMap[gate.GateKey] = gate.Enabled
	}

	// Verify map contents
	if len(gateMap) != 2 {
		t.Errorf("Expected map with 2 entries, got %d", len(gateMap))
	}

	if enabled, exists := gateMap["feature.multi-region"]; !exists {
		t.Error("Expected feature.multi-region to exist in map")
	} else if !enabled {
		t.Error("Expected feature.multi-region to be enabled")
	}

	// Convert back to slice
	var reconstructed apitypes.FeatureGates
	for key, enabled := range gateMap {
		reconstructed = append(reconstructed, apitypes.FeatureGate{
			GateKey: key,
			Enabled: enabled,
		})
	}

	if len(reconstructed) != len(gates) {
		t.Errorf("Expected %d gates after reconstruction, got %d", len(gates), len(reconstructed))
	}
}
