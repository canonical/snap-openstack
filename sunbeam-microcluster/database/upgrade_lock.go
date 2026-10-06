package database

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"time"

	"github.com/canonical/lxd/shared/api"
)

// LockRow is the single row of upgrade_lock. token is monotonically
// increasing across acquires; holder_id identifies the current holder;
// expires_at is a unix timestamp (0 = no active holder).
type LockRow struct {
	Token     int64
	HolderID  string
	ExpiresAt int64
}

// LockHeldError is returned by AcquireUpgradeLock when another live holder
// owns the lock. caller_holder identifies the existing holder.
type LockHeldError struct {
	HolderID string
}

func (e *LockHeldError) Error() string {
	return fmt.Sprintf("upgrade lock held by %q", e.HolderID)
}

// TokenMismatchError is returned when a write carries a fencing token that
// does not match the lock's current token — i.e. the caller's lock has
// expired and been re-acquired by someone else.
type TokenMismatchError struct {
	Expected int64
	Actual   int64
}

func (e *TokenMismatchError) Error() string {
	return fmt.Sprintf("fencing token mismatch: have %d, lock at %d", e.Expected, e.Actual)
}

const lockRowID = 1

// GetUpgradeLockRow returns the single lock row.
func GetUpgradeLockRow(ctx context.Context, tx *sql.Tx) (LockRow, error) {
	row := tx.QueryRowContext(ctx,
		`SELECT token, holder_id, expires_at FROM upgrade_lock WHERE id = ?`, lockRowID)

	var r LockRow
	err := row.Scan(&r.Token, &r.HolderID, &r.ExpiresAt)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return LockRow{}, api.StatusErrorf(500, "upgrade_lock row missing — schema not applied")
		}
		return LockRow{}, fmt.Errorf("failed to read upgrade_lock: %w", err)
	}
	return r, nil
}

// AcquireUpgradeLock claims the lock for holderID with the given TTL. Returns
// the new fencing token. If a live holder exists (expires_at > now), returns
// LockHeldError. Token is always current_token + 1 — monotonic across
// releases and expiries, so a stale holder's later writes are rejectable.
func AcquireUpgradeLock(ctx context.Context, tx *sql.Tx, holderID string, ttlSec int) (int64, error) {
	now := time.Now().Unix()
	expiresAt := now + int64(ttlSec)

	current, err := GetUpgradeLockRow(ctx, tx)
	if err != nil {
		return 0, err
	}

	if current.ExpiresAt > now && current.HolderID != "" {
		return 0, &LockHeldError{HolderID: current.HolderID}
	}

	newToken := current.Token + 1
	res, err := tx.ExecContext(ctx,
		`UPDATE upgrade_lock SET token = ?, holder_id = ?, expires_at = ? WHERE id = ?`,
		newToken, holderID, expiresAt, lockRowID)
	if err != nil {
		return 0, fmt.Errorf("failed to acquire upgrade_lock: %w", err)
	}
	if n, _ := res.RowsAffected(); n != 1 {
		return 0, fmt.Errorf("upgrade_lock update affected %d rows, expected 1", n)
	}

	return newToken, nil
}

// RefreshUpgradeLock extends the lock's TTL. Must be called by the holder
// before the old TTL expires, or the lock becomes acquirable by someone else.
// Returns TokenMismatchError if the caller's token is stale.
func RefreshUpgradeLock(ctx context.Context, tx *sql.Tx, token int64, ttlSec int) error {
	now := time.Now().Unix()
	expiresAt := now + int64(ttlSec)

	res, err := tx.ExecContext(ctx,
		`UPDATE upgrade_lock SET expires_at = ? WHERE id = ? AND token = ?`,
		expiresAt, lockRowID, token)
	if err != nil {
		return fmt.Errorf("failed to refresh upgrade_lock: %w", err)
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		row, _ := GetUpgradeLockRow(ctx, tx)
		return &TokenMismatchError{Expected: token, Actual: row.Token}
	}
	return nil
}

// ReleaseUpgradeLock releases the lock. Returns TokenMismatchError if the
// caller's token is stale (someone else acquired after expiry). holder_id and
// expires_at are cleared; token is preserved for monotonicity.
func ReleaseUpgradeLock(ctx context.Context, tx *sql.Tx, token int64) error {
	res, err := tx.ExecContext(ctx,
		`UPDATE upgrade_lock SET holder_id = '', expires_at = 0 WHERE id = ? AND token = ?`,
		lockRowID, token)
	if err != nil {
		return fmt.Errorf("failed to release upgrade_lock: %w", err)
	}
	n, _ := res.RowsAffected()
	if n == 0 {
		row, _ := GetUpgradeLockRow(ctx, tx)
		return &TokenMismatchError{Expected: token, Actual: row.Token}
	}
	return nil
}

// VerifyToken returns nil if the given token matches the lock's current token
// and the lock is live (not expired). Otherwise returns TokenMismatchError.
// Used as the CAS precondition for state writes.
func VerifyToken(ctx context.Context, tx *sql.Tx, token int64) error {
	row, err := GetUpgradeLockRow(ctx, tx)
	if err != nil {
		return err
	}
	if row.Token != token {
		return &TokenMismatchError{Expected: token, Actual: row.Token}
	}
	now := time.Now().Unix()
	if row.ExpiresAt <= now {
		return &TokenMismatchError{Expected: token, Actual: row.Token}
	}
	return nil
}
