package sunbeam

import (
	"context"
	"fmt"
	"sync"
	"time"

	"github.com/canonical/go-snapctl"
	"github.com/canonical/lxd/shared/logger"
	"github.com/canonical/microcluster/v2/state"
)

const (
	// syncInterval is how often to check for feature gate changes
	syncInterval = 5 * time.Second

	// syncDebounce is the time to wait after we set a value before syncing again
	// This prevents circular triggers: snap set -> hook -> cluster -> daemon -> snap set
	syncDebounce = 2 * time.Second
)

// snapctlClient is an interface for snap configuration operations
type snapctlClient interface {
	Set(key, value string) error
	Unset(key string) error
}

// goSnapctlClient uses the go-snapctl library
type goSnapctlClient struct{}

func (c *goSnapctlClient) Set(key, value string) error {
	return snapctl.Set(key, value).Run()
}

func (c *goSnapctlClient) Unset(key string) error {
	return snapctl.Unset(key).Run()
}

// featureGateSyncer manages synchronization of feature gates from cluster to snap config
type featureGateSyncer struct {
	state          state.State
	lastSyncTime   time.Time
	lastKnownGates map[string]bool
	mu             sync.RWMutex
	snapctl        snapctlClient
}

// newFeatureGateSyncer creates a new feature gate syncer
func newFeatureGateSyncer(s state.State) *featureGateSyncer {
	return &featureGateSyncer{
		state:          s,
		lastKnownGates: make(map[string]bool),
		snapctl:        &goSnapctlClient{}, // Use go-snapctl in production
	}
}

// StartFeatureGateSync starts a background goroutine that syncs feature gates
// from the cluster database to the local snap configuration.
func StartFeatureGateSync(ctx context.Context, s state.State) {
	syncer := newFeatureGateSyncer(s)

	go syncer.syncLoop(ctx)

	logger.Info("Started feature gate sync watcher")
}

// syncLoop periodically checks for changes in the cluster feature gates
// and updates the local snap configuration
func (fgs *featureGateSyncer) syncLoop(ctx context.Context) {
	ticker := time.NewTicker(syncInterval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			logger.Info("Stopping feature gate sync watcher")
			return
		case <-ticker.C:
			if err := fgs.syncOnce(ctx); err != nil {
				logger.Errorf("Failed to sync feature gates: %v", err)
			}
		}
	}
}

// syncOnce performs a single sync operation
func (fgs *featureGateSyncer) syncOnce(ctx context.Context) error {
	fgs.mu.Lock()
	defer fgs.mu.Unlock()

	// Check deployment type on every iteration to handle race conditions
	// In MAAS mode, we want one-way sync (snap -> cluster) but not writeback (cluster -> snap)
	// because each node manages its own snap configuration independently
	if fgs.state != nil {
		deploymentType, err := GetConfig(ctx, fgs.state, "deployment.type")
		if err == nil && deploymentType == "maas" {
			// Skip sync for MAAS deployments
			return nil
		}
	}
	// If we can't read deployment type (key doesn't exist yet, state is nil, or other error),
	// assume local deployment and continue with sync for backward compatibility

	// Debounce: if we recently set values, don't sync yet
	// This prevents circular triggers
	if time.Since(fgs.lastSyncTime) < syncDebounce {
		return nil
	}

	// Get current feature gates from cluster
	gates, err := ListFeatureGates(ctx, fgs.state)
	if err != nil {
		return fmt.Errorf("failed to list feature gates: %w", err)
	}

	// Build map of current gates from cluster
	clusterGates := make(map[string]bool)
	for _, gate := range gates {
		clusterGates[gate.GateKey] = gate.Enabled
	}

	// Check for changes compared to last known state
	changed := false
	for gateKey, enabled := range clusterGates {
		if lastEnabled, exists := fgs.lastKnownGates[gateKey]; !exists || lastEnabled != enabled {
			changed = true
			break
		}
	}

	// Check if any gates were removed
	for gateKey := range fgs.lastKnownGates {
		if _, exists := clusterGates[gateKey]; !exists {
			changed = true
			break
		}
	}

	// If nothing changed, we're done
	if !changed {
		return nil
	}

	logger.Debugf("Feature gates changed, syncing to snap config")

	// Update snap config for each gate
	for gateKey, enabled := range clusterGates {
		if err := fgs.setSnapConfig(gateKey, enabled); err != nil {
			logger.Errorf("Failed to set snap config for %s: %v", gateKey, err)
			// Continue with other gates even if one fails
		}
	}

	// Remove gates that are no longer in cluster
	for gateKey := range fgs.lastKnownGates {
		if _, exists := clusterGates[gateKey]; !exists {
			if err := fgs.unsetSnapConfig(gateKey); err != nil {
				logger.Errorf("Failed to unset snap config for %s: %v", gateKey, err)
			}
		}
	}

	// Update our known state and sync time
	fgs.lastKnownGates = clusterGates
	fgs.lastSyncTime = time.Now()

	return nil
}

// hasFeaturePrefix checks if a gate key starts with "feature."
func hasFeaturePrefix(gateKey string) bool {
	return len(gateKey) > 8 && gateKey[:8] == "feature."
}

// setSnapConfig sets a feature gate in the snap configuration
func (fgs *featureGateSyncer) setSnapConfig(gateKey string, enabled bool) error {
	// Ensure gateKey has "feature." prefix for snap config
	// API may accept keys with or without prefix, so normalize here
	if !hasFeaturePrefix(gateKey) {
		gateKey = "feature." + gateKey
	}

	enabledStr := "false"
	if enabled {
		enabledStr = "true"
	}

	if err := fgs.snapctl.Set(gateKey, enabledStr); err != nil {
		return fmt.Errorf("snapctl set failed: %w", err)
	}

	logger.Infof("Synced feature gate to snap: %s=%t", gateKey, enabled)
	return nil
}

// unsetSnapConfig removes a feature gate from the snap configuration
func (fgs *featureGateSyncer) unsetSnapConfig(gateKey string) error {
	// Ensure gateKey has "feature." prefix for snap config
	// API may accept keys with or without prefix, so normalize here
	if !hasFeaturePrefix(gateKey) {
		gateKey = "feature." + gateKey
	}

	if err := fgs.snapctl.Unset(gateKey); err != nil {
		return fmt.Errorf("snapctl unset failed: %w", err)
	}

	logger.Infof("Removed feature gate from snap: %s", gateKey)
	return nil
}

// ForceSync forces an immediate sync, bypassing the debounce timer.
// This is useful for testing or when you know changes are safe.
func (fgs *featureGateSyncer) ForceSync(ctx context.Context) error {
	fgs.mu.Lock()
	defer fgs.mu.Unlock()

	// Temporarily clear the debounce
	fgs.lastSyncTime = time.Time{}

	return fgs.syncOnce(ctx)
}
