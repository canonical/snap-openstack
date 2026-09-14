// Package sunbeam provides the interface to talk to database.
package sunbeam

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"net/http"
	"time"

	"github.com/canonical/lxd/shared/api"
	"github.com/canonical/microcluster/v2/state"

	"github.com/canonical/snap-openstack/sunbeam-microcluster/database"
)

// Default lock TTL in seconds. Refreshed every ~30s by the coordinator.
const UpgradeLockTTLSeconds = 60

// UpgradeStateKey is the config-table key under which the upgrade state JSON
// blob is stored. This blob holds hop_history + active_hop as
// a reference (hop_history_index); there is no separate active_hop object.
const UpgradeStateKey = "upgrade_state"

// AcquireUpgradeLock claims the advisory lock for holderID. Returns the
// fencing token that must be passed to every subsequent state write.
func AcquireUpgradeLock(ctx context.Context, s state.State, holderID string) (int64, error) {
	var token int64
	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		var acquireErr error
		token, acquireErr = database.AcquireUpgradeLock(ctx, tx, holderID, UpgradeLockTTLSeconds)
		return acquireErr
	})
	return token, err
}

// RefreshUpgradeLock extends the lock's TTL. Called by the coordinator's
// heartbeat loop. Returns an error if the caller's token is stale.
func RefreshUpgradeLock(ctx context.Context, s state.State, token int64) error {
	return s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		return database.RefreshUpgradeLock(ctx, tx, token, UpgradeLockTTLSeconds)
	})
}

// ReleaseUpgradeLock releases the lock. Called on clean exit (finalize,
// abandon, or command completion that doesn't leave a hop in flight).
func ReleaseUpgradeLock(ctx context.Context, s state.State, token int64) error {
	return s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		return database.ReleaseUpgradeLock(ctx, tx, token)
	})
}

// GetUpgradeState returns the persisted upgrade state JSON. Returns
// api.StatusError(404) if no state has been written yet.
func GetUpgradeState(ctx context.Context, s state.State) (string, error) {
	var value string
	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		record, dbErr := database.GetConfigItem(ctx, tx, UpgradeStateKey)
		if dbErr != nil {
			return dbErr
		}
		value = record.Value
		return nil
	})
	if err != nil {
		if apiStatusIsNotFound(err) {
			return "", api.StatusErrorf(http.StatusNotFound, "no upgrade state")
		}
		return "", err
	}
	return value, nil
}

// UpdateUpgradeState writes the upgrade state JSON, but only if the caller's
// fencing token matches the lock's current token. The token check and the
// write happen in the same transaction — SIGKILL between them is impossible.
// Returns database.TokenMismatchError if the token is stale.
func UpdateUpgradeState(ctx context.Context, s state.State, token int64, stateJSON string) error {
	return s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		if err := database.VerifyToken(ctx, tx, token); err != nil {
			return err
		}
		item := database.ConfigItem{Key: UpgradeStateKey, Value: stateJSON}
		if _, err := database.GetConfigItem(ctx, tx, UpgradeStateKey); err != nil {
			if apiStatusIsNotFound(err) {
				if _, cerr := database.CreateConfigItem(ctx, tx, item); cerr != nil {
					return fmt.Errorf("failed to create upgrade state: %w", cerr)
				}
				return nil
			}
			return err
		}
		if err := database.UpdateConfigItem(ctx, tx, UpgradeStateKey, item); err != nil {
			return fmt.Errorf("failed to update upgrade state: %w", err)
		}
		return nil
	})
}

// IsUpgradeActive returns true if a hop is in progress. Used by the mutating-
// command guard to block conflicting operations during an upgrade.
// A hop is "in progress" if the lock is held by a live holder.
func IsUpgradeActive(ctx context.Context, s state.State) (bool, error) {
	var active bool
	err := s.Database().Transaction(ctx, func(ctx context.Context, tx *sql.Tx) error {
		row, err := database.GetUpgradeLockRow(ctx, tx)
		if err != nil {
			return err
		}
		if row.HolderID == "" {
			active = false
			return nil
		}
		active = row.ExpiresAt > time.Now().Unix()
		return nil
	})
	return active, err
}

// apiStatusIsNotFound returns true if err is an lxd api StatusError with
// 404 status.
func apiStatusIsNotFound(err error) bool {
	var se interface{ Status() int }
	if errors.As(err, &se) {
		return se.Status() == http.StatusNotFound
	}
	return false
}
