package sunbeam

import (
	"encoding/json"
	"net/http"
	"strings"

	"github.com/canonical/lxd/shared/api"
	"github.com/canonical/microcluster/state"

	"github.com/openstack-snaps/snap-openstack/sunbeam-microcluster/api/types"
)

const tfstatePrefix = "tfstate-"
const tflockPrefix = "tflock-"

// GetTerraformStates returns the list of terraform states from the database
func GetTerraformStates(s *state.State) ([]string, error) {
	prefix := tfstatePrefix
	states, err := GetConfigItemKeys(s, &prefix)
	if err != nil {
		return nil, err
	}

	plans := make([]string, len(states))
	for i, state := range states {
		plans[i] = strings.TrimPrefix(state, tfstatePrefix)
	}

	return plans, nil
}

// GetTerraformState returns the terraform state from the database
func GetTerraformState(s *state.State, name string) (string, error) {
	tfstateKey := tfstatePrefix + name
	state, err := GetConfig(s, tfstateKey)
	return state, err
}

// UpdateTerraformState updates the terraform state record in the database
func UpdateTerraformState(s *state.State, name string, lockID string, state string) (types.Lock, error) {
	var dbLock types.Lock

	tflockKey := tflockPrefix + name
	lockInDb, err := GetConfig(s, tflockKey)
	if err != nil {
		return dbLock, err
	}

	err = json.Unmarshal([]byte(lockInDb), &dbLock)
	if err != nil {
		return dbLock, err
	}

	if lockID != dbLock.ID {
		return dbLock, api.StatusErrorf(http.StatusConflict, "Conflict in Lock ID")
	}

	tfstateKey := tfstatePrefix + name
	err = UpdateConfig(s, tfstateKey, state)
	if err != nil {
		return dbLock, err
	}

	return dbLock, nil
}

// DeleteTerraformState deletes the terraform state from the database
func DeleteTerraformState(s *state.State, name string) error {
	tfstateKey := tfstatePrefix + name
	err := DeleteConfig(s, tfstateKey)
	return err
}

// GetTerraformLocks returns the list of terraform locks from the database
func GetTerraformLocks(s *state.State) ([]string, error) {
	prefix := tflockPrefix
	locks, err := GetConfigItemKeys(s, &prefix)
	if err != nil {
		return nil, err
	}

	trimmedLocks := make([]string, len(locks))
	for i, state := range locks {
		trimmedLocks[i] = strings.TrimPrefix(state, tflockPrefix)
	}

	return trimmedLocks, nil
}

// GetTerraformLock returns the terraform lock from the database
func GetTerraformLock(s *state.State, name string) (string, error) {
	tflockKey := tflockPrefix + name
	lock, err := GetConfig(s, tflockKey)
	return lock, err
}

// UpdateTerraformLock updates the terraform lock record in the database
func UpdateTerraformLock(s *state.State, name string, lock string) (types.Lock, error) {
	var reqLock types.Lock
	var dbLock types.Lock

	err := json.Unmarshal([]byte(lock), &reqLock)
	if err != nil {
		return dbLock, err
	}

	tflockKey := tflockPrefix + name
	lockInDb, err := GetConfig(s, tflockKey)
	if err != nil {
		if err, ok := err.(api.StatusError); ok {
			// No Lock exists, add lock details in DB
			if err.Status() == http.StatusNotFound {
				j, err := json.Marshal(reqLock)
				if err != nil {
					return dbLock, err
				}

				err = UpdateConfig(s, tflockKey, string(j))
				return dbLock, err
			}
		}
		return dbLock, err
	}

	err = json.Unmarshal([]byte(lockInDb), &dbLock)
	if err != nil {
		return dbLock, err
	}

	// If the lock from DB and request are same, send http 423
	if dbLock.ID == reqLock.ID && dbLock.Operation == reqLock.Operation && dbLock.Who == reqLock.Who {
		return dbLock, api.StatusErrorf(http.StatusLocked, "Already locked with same ID")
	}

	// Already locked and request has different lockid, send http 409
	return dbLock, api.StatusErrorf(http.StatusConflict, "Conflict in Lock ID")
}

// DeleteTerraformLock deletes the terraform lock from the database
func DeleteTerraformLock(s *state.State, name string, lock string) (types.Lock, error) {
	var reqLock types.Lock
	var dbLock types.Lock

	err := json.Unmarshal([]byte(lock), &reqLock)
	if err != nil {
		return dbLock, err
	}

	tflockKey := tflockPrefix + name
	lockInDb, err := GetConfig(s, tflockKey)
	if err != nil {
		if err, ok := err.(api.StatusError); ok {
			// No Lock exists to unlock, send 200: OK
			if err.Status() == http.StatusNotFound {
				return dbLock, nil
			}
		}
		return dbLock, err
	}

	err = json.Unmarshal([]byte(lockInDb), &dbLock)
	if err != nil {
		return dbLock, err
	}

	// If the lock from DB and request are same, clear the lock from DB
	if dbLock.ID == reqLock.ID && dbLock.Operation == reqLock.Operation && dbLock.Who == reqLock.Who {
		err = DeleteConfig(s, tflockKey)
		return dbLock, err
	}

	// Request has different lock id than in database, send http 409
	return dbLock, api.StatusErrorf(http.StatusConflict, "Conflict in Lock ID")
}
