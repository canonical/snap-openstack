package sunbeam

import (
	"errors"
	"fmt"
	"net/http"
	"testing"

	"github.com/canonical/lxd/shared/api"
)

func TestUpdateTerraformLockAfterConcurrentCreate(t *testing.T) {
	tests := []struct {
		name         string
		requestLock  string
		existingLock string
		status       int
	}{
		{
			name:         "same lock",
			requestLock:  `{"ID":"lock-id","Operation":"join","Who":"node-1"}`,
			existingLock: `{"ID":"lock-id","Operation":"join","Who":"node-1"}`,
			status:       http.StatusLocked,
		},
		{
			name:         "different lock",
			requestLock:  `{"ID":"lock-id","Operation":"join","Who":"node-1"}`,
			existingLock: `{"ID":"other-id","Operation":"join","Who":"node-2"}`,
			status:       http.StatusConflict,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			getCalls := 0
			getLock := func() (string, error) {
				getCalls++
				if getCalls == 1 {
					return "", api.StatusErrorf(http.StatusNotFound, "Lock not found")
				}

				return test.existingLock, nil
			}
			createLock := func(string) error {
				return fmt.Errorf(
					"Failed to record config item: %w",
					api.StatusErrorf(http.StatusConflict, "Lock already exists"),
				)
			}

			_, err := updateTerraformLock(test.requestLock, getLock, createLock)
			if !api.StatusErrorCheck(err, test.status) {
				t.Fatalf("Expected status %d, got %v", test.status, err)
			}
			if getCalls != 2 {
				t.Fatalf("Expected the stored lock to be read twice, got %d", getCalls)
			}
		})
	}
}

func TestUpdateTerraformLockReturnsCreateError(t *testing.T) {
	createErr := errors.New("database unavailable")
	getCalls := 0
	getLock := func() (string, error) {
		getCalls++
		return "", api.StatusErrorf(http.StatusNotFound, "Lock not found")
	}
	createLock := func(string) error {
		return createErr
	}

	_, err := updateTerraformLock(
		`{"ID":"lock-id","Operation":"join","Who":"node-1"}`,
		getLock,
		createLock,
	)
	if !errors.Is(err, createErr) {
		t.Fatalf("Expected create error, got %v", err)
	}
	if getCalls != 1 {
		t.Fatalf("Expected no retry for create error, got %d reads", getCalls)
	}
}
