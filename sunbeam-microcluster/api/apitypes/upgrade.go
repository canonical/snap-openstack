// Package apitypes provides shared types and structs.
package apitypes

// AcquireUpgradeLockRequest is the body for POST /1.0/upgrade/lock.
type AcquireUpgradeLockRequest struct {
	// HolderID identifies the process holding the lock (e.g. hostname + pid).
	HolderID string `json:"holder_id" yaml:"holder_id"`
}

// AcquireUpgradeLockResponse is returned by a successful lock acquire.
type AcquireUpgradeLockResponse struct {
	// Token is the fencing token. Must be passed on every subsequent state
	// write; clusterd rejects writes whose token ≠ the lock's current token.
	Token int64 `json:"token" yaml:"token"`
}

// RefreshUpgradeLockRequest is the body for PUT /1.0/upgrade/lock.
type RefreshUpgradeLockRequest struct {
	// Token is the caller's fencing token, proving current ownership.
	Token int64 `json:"token" yaml:"token"`
}

// ReleaseUpgradeLockRequest is the body for DELETE /1.0/upgrade/lock.
type ReleaseUpgradeLockRequest struct {
	// Token is the caller's fencing token, proving current ownership.
	Token int64 `json:"token" yaml:"token"`
}

// UpdateUpgradeStateRequest is the body for PUT /1.0/upgrade/state.
type UpdateUpgradeStateRequest struct {
	// Token is the caller's fencing token. Must match the lock's current
	// token or the write is rejected (database.TokenMismatchError).
	Token int64 `json:"token" yaml:"token"`
	// State is the JSON-encoded upgrade state blob (§6.1 of the spec).
	State string `json:"state" yaml:"state"`
}

// IsUpgradeActiveResponse is returned by GET /1.0/upgrade/active.
type IsUpgradeActiveResponse struct {
	Active bool `json:"active" yaml:"active"`
}
