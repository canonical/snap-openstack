package database

import (
	"context"
	"database/sql"
	"errors"
	"testing"
	"time"

	_ "github.com/mattn/go-sqlite3"
)

// newTestDB returns an in-memory SQLite with the upgrade_lock and config
// schemas applied. Each test gets a fresh DB (sqlite3 ":memory:" with
// ?cache=shared would leak across tests).
func newTestDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite3", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	ctx := context.Background()
	for _, fn := range []func(context.Context, *sql.Tx) error{
		ConfigSchemaUpdate,
		UpgradeLockSchemaUpdate,
	} {
		if err := dbTx(ctx, db, fn); err != nil {
			t.Fatalf("schema apply: %v", err)
		}
	}
	return db
}

func dbTx(ctx context.Context, db *sql.DB, fn func(context.Context, *sql.Tx) error) error {
	tx, err := db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	if err := fn(ctx, tx); err != nil {
		_ = tx.Rollback()
		return err
	}
	return tx.Commit()
}

// TestAcquireLockReturnsMonotonicToken is the core invariant: token always
// increases across acquires, so a stale holder is detectable.
func TestAcquireLockReturnsMonotonicToken(t *testing.T) {
	ctx := context.Background()
	db := newTestDB(t)
	defer db.Close()

	var tok1, tok2 int64
	if err := dbTx(ctx, db, func(ctx context.Context, tx *sql.Tx) error {
		var err error
		tok1, err = AcquireUpgradeLock(ctx, tx, "host-a", 60)
		return err
	}); err != nil {
		t.Fatalf("first acquire: %v", err)
	}

	if err := dbTx(ctx, db, func(ctx context.Context, tx *sql.Tx) error {
		var err error
		tok2, err = AcquireUpgradeLock(ctx, tx, "host-a", 60)
		return err
	}); err != nil {
		var held *LockHeldError
		if !errors.As(err, &held) {
			t.Fatalf("second acquire while held should be LockHeld, got %v", err)
		}
		// lock is live — expected; simulate expiry by setting expires_at to 0
		if err := dbTx(ctx, db, func(ctx context.Context, tx *sql.Tx) error {
			_, err := tx.ExecContext(ctx, `UPDATE upgrade_lock SET expires_at = 0 WHERE id = 1`)
			return err
		}); err != nil {
			t.Fatalf("force-expire: %v", err)
		}
		if err := dbTx(ctx, db, func(ctx context.Context, tx *sql.Tx) error {
			var err error
			tok2, err = AcquireUpgradeLock(ctx, tx, "host-b", 60)
			return err
		}); err != nil {
			t.Fatalf("acquire after expiry: %v", err)
		}
	}

	if tok2 != tok1+1 {
		t.Fatalf("token not monotonic: tok1=%d tok2=%d, expected %d", tok1, tok2, tok1+1)
	}
}

// TestVerifyTokenRejectsStaleToken is THE G1 invariant: after the lock
// expires and is re-acquired, writes carrying the old token are rejected.
func TestVerifyTokenRejectsStaleToken(t *testing.T) {
	ctx := context.Background()
	db := newTestDB(t)
	defer db.Close()

	var staleTok, liveTok int64
	if err := dbTx(ctx, db, func(ctx context.Context, tx *sql.Tx) error {
		var err error
		staleTok, err = AcquireUpgradeLock(ctx, tx, "proc-a", 60)
		return err
	}); err != nil {
		t.Fatalf("acquire A: %v", err)
	}

	// Force expiry, acquire by B.
	if err := dbTx(ctx, db, func(ctx context.Context, tx *sql.Tx) error {
		_, err := tx.ExecContext(ctx, `UPDATE upgrade_lock SET expires_at = ? WHERE id = 1`, time.Now().Unix()-1)
		return err
	}); err != nil {
		t.Fatalf("expire A: %v", err)
	}
	if err := dbTx(ctx, db, func(ctx context.Context, tx *sql.Tx) error {
		var err error
		liveTok, err = AcquireUpgradeLock(ctx, tx, "proc-b", 60)
		return err
	}); err != nil {
		t.Fatalf("acquire B: %v", err)
	}

	// Stale token (A's) must be rejected.
	var staleErr *TokenMismatchError
	if err := dbTx(ctx, db, func(ctx context.Context, tx *sql.Tx) error {
		return VerifyToken(ctx, tx, staleTok)
	}); !errors.As(err, &staleErr) {
		t.Fatalf("stale token %d must be rejected with TokenMismatchError, got %v", staleTok, err)
	}
	if staleErr.Actual != liveTok {
		t.Fatalf("mismatch error reports actual=%d, want %d", staleErr.Actual, liveTok)
	}

	// Live token (B's) must pass.
	if err := dbTx(ctx, db, func(ctx context.Context, tx *sql.Tx) error {
		return VerifyToken(ctx, tx, liveTok)
	}); err != nil {
		t.Fatalf("live token %d must verify, got %v", liveTok, err)
	}
}

// TestReleaseThenAcquireContinuesTokenCount confirms release preserves the
// token counter for monotonicity — release does not reset to 0.
func TestReleaseThenAcquireContinuesTokenCount(t *testing.T) {
	ctx := context.Background()
	db := newTestDB(t)
	defer db.Close()

	var tok1 int64
	if err := dbTx(ctx, db, func(ctx context.Context, tx *sql.Tx) error {
		var err error
		tok1, err = AcquireUpgradeLock(ctx, tx, "h", 60)
		return err
	}); err != nil {
		t.Fatalf("acquire: %v", err)
	}
	if err := dbTx(ctx, db, func(ctx context.Context, tx *sql.Tx) error {
		return ReleaseUpgradeLock(ctx, tx, tok1)
	}); err != nil {
		t.Fatalf("release: %v", err)
	}

	var tok2 int64
	if err := dbTx(ctx, db, func(ctx context.Context, tx *sql.Tx) error {
		var err error
		tok2, err = AcquireUpgradeLock(ctx, tx, "h", 60)
		return err
	}); err != nil {
		t.Fatalf("re-acquire: %v", err)
	}
	if tok2 != tok1+1 {
		t.Fatalf("token must continue after release: tok1=%d tok2=%d, want %d", tok1, tok2, tok1+1)
	}
}

// TestRefreshRejectsStaleToken confirms refresh (heartbeat) fails loudly when
// the caller's token is stale — so the coordinator knows to stop.
func TestRefreshRejectsStaleToken(t *testing.T) {
	ctx := context.Background()
	db := newTestDB(t)
	defer db.Close()

	var tok1 int64
	if err := dbTx(ctx, db, func(ctx context.Context, tx *sql.Tx) error {
		var err error
		tok1, err = AcquireUpgradeLock(ctx, tx, "a", 60)
		return err
	}); err != nil {
		t.Fatalf("acquire: %v", err)
	}
	// Force expiry + re-acquire by someone else.
	if err := dbTx(ctx, db, func(ctx context.Context, tx *sql.Tx) error {
		_, err := tx.ExecContext(ctx, `UPDATE upgrade_lock SET expires_at = ? WHERE id = 1`, time.Now().Unix()-1)
		return err
	}); err != nil {
		t.Fatalf("expire: %v", err)
	}
	if err := dbTx(ctx, db, func(ctx context.Context, tx *sql.Tx) error {
		_, err := AcquireUpgradeLock(ctx, tx, "b", 60)
		return err
	}); err != nil {
		t.Fatalf("re-acquire: %v", err)
	}
	// Stale refresh must fail.
	var mismatch *TokenMismatchError
	if err := dbTx(ctx, db, func(ctx context.Context, tx *sql.Tx) error {
		return RefreshUpgradeLock(ctx, tx, tok1, 60)
	}); !errors.As(err, &mismatch) {
		t.Fatalf("refresh with stale token must fail with TokenMismatchError, got %v", err)
	}
}
