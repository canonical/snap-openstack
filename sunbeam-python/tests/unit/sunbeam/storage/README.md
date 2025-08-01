# Storage Backend Management Unit Tests

This directory contains comprehensive unit tests for the sunbeam storage backend management system.

## Test Structure

### Core Components Tested

1. **`test_basestorage.py`** - Tests for the core storage backend functionality:
   - `ExtendedJujuHelper` - Enhanced Juju operations with configuration management
   - `StorageBackendService` - Main service layer for backend operations
   - `StorageBackendBase` - Base class for all storage backend implementations
   - `StorageBackendConfig` and `StorageBackendInfo` - Data models
   - Exception hierarchy (`StorageBackendException`, `BackendNotFoundException`, etc.)

2. **`test_registry.py`** - Tests for the storage backend registry system:
   - `StorageBackendRegistry` - Dynamic backend discovery and CLI registration
   - Backend loading from `storage/backends/` directory
   - CLI command registration and management
   - Global registry instance behavior

3. **`test_steps.py`** - Tests for deployment and management steps:
   - `ValidateConfigStep` - Configuration validation
   - `CheckBackendExistsStep` - Backend existence checking
   - `ValidateBackendExistsStep` - Backend existence validation for removal
   - `DeployCharmStep` - Charm deployment operations
   - `IntegrateWithCinderVolumeStep` - Integration with Cinder Volume
   - `WaitForReadyStep` - Application readiness waiting
   - `RemoveBackendStep` - Backend removal operations

4. **`backends/test_hitachi.py`** - Tests for the Hitachi storage backend:
   - `HitachiConfig` - Configuration model with validation
   - `HitachiBackend` - Backend implementation
   - Hitachi-specific deployment steps
   - Configuration prompting and validation

## Test Coverage

### Key Areas Covered

- **Configuration Management**: Dynamic configuration retrieval, setting, and resetting
- **Backend Detection**: Charm name normalization and backend type identification
- **Service Layer Operations**: Backend listing, existence checking, and management
- **CLI Command Registration**: Dynamic command discovery and registration
- **Error Handling**: Comprehensive exception testing for all error scenarios
- **Step-Based Operations**: Deployment, integration, and removal workflows
- **Pydantic Model Validation**: Configuration model validation and error handling

### Test Patterns Used

- **Mock-based Testing**: Extensive use of `unittest.mock` for isolating components
- **Patch Decorators**: Strategic patching of external dependencies (Juju, subprocess, etc.)
- **Parameterized Tests**: Using `subTest()` for testing multiple input scenarios
- **Exception Testing**: Verifying proper exception raising and handling
- **State Verification**: Ensuring correct object state after operations

## Running Tests

### Full Test Suite
```bash
tox -e cover
```

### Specific Test Files
```bash
tox -e cover -- tests/unit/sunbeam/storage/test_basestorage.py
tox -e cover -- tests/unit/sunbeam/storage/test_registry.py
tox -e cover -- tests/unit/sunbeam/storage/test_steps.py
tox -e cover -- tests/unit/sunbeam/storage/backends/test_hitachi.py
```

### Specific Test Classes
```bash
tox -e cover -- tests/unit/sunbeam/storage/test_basestorage.py::TestStorageBackendService
tox -e cover -- tests/unit/sunbeam/storage/test_registry.py::TestStorageBackendRegistry
```

## Test Configuration

### Fixtures (`conftest.py`)
- `mock_deployment` - Provides mock deployment objects
- `mock_juju_helper` - Mocks JujuHelper operations
- `mock_storage_service` - Mocks StorageBackendService
- `reset_global_registry` - Ensures clean registry state between tests

### Mock Strategies
- **External Dependencies**: Juju CLI operations, subprocess calls, file system operations
- **Service Layer**: Storage backend service operations for isolated testing
- **Configuration Models**: Pydantic model validation and field access
- **Rich Console**: User interface components for CLI testing

## Key Testing Principles

1. **Isolation**: Each test is independent and doesn't rely on external state
2. **Mocking**: External dependencies are mocked to ensure fast, reliable tests
3. **Coverage**: All public methods and error paths are tested
4. **Realistic Scenarios**: Tests reflect actual usage patterns and edge cases
5. **Maintainability**: Tests are structured to be easy to understand and modify

## Test Data and Scenarios

### Common Test Scenarios
- Backend existence checking (exists/doesn't exist)
- Configuration validation (valid/invalid inputs)
- Service operations (success/failure paths)
- CLI command registration (with/without backends)
- Step execution (completion/failure/timeout scenarios)

### Mock Data Patterns
- Backend info objects with realistic charm names and statuses
- Configuration objects with valid and invalid field combinations
- Juju status responses mimicking real deployment states
- Error scenarios matching actual Juju and system failures

## Maintenance Notes

- Tests are aligned with the actual implementation API
- Mock objects match the real object interfaces
- Test data reflects realistic deployment scenarios
- Error messages and exception types match the implementation
- Tests are updated when the underlying implementation changes

This comprehensive test suite ensures the reliability and maintainability of the storage backend management system while providing confidence for future development and refactoring efforts.
