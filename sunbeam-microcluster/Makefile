.PHONY: default
default: build

# Build targets.
.PHONY: build
build:
	CGO_LDFLAGS_ALLOW="-Wl,-z,now" go install -v ./cmd/sunbeamd

# Format code
fmt: 
	gofmt -s -w .

# Testing targets.
.PHONY: check
check: check-static check-unit

.PHONY: check-unit
check-unit:
	CGO_LDFLAGS_ALLOW="-Wl,-z,now" go test ./...

.PHONY: check-system
check-system:
	true

.PHONY: check-static
check-static:
ifeq ($(shell command -v golangci-lint 2> /dev/null),)
	go install github.com/golangci/golangci-lint/cmd/golangci-lint@v1.64.8
endif
ifeq ($(shell command -v revive 2> /dev/null),)
	go install github.com/mgechev/revive@latest
endif
	CGO_LDFLAGS_ALLOW="-Wl,-z,now" golangci-lint run --timeout 5m
	revive -set_exit_status ./...

# Update targets.
.PHONY: update-gomod
update-gomod:
	go get ./...
	go mod tidy

# Update lxd-generate generated database helpers.
.PHONY: update-schema
update-schema:
	go generate ./...
	gofmt -s -w ./database/
	goimports -w ./database/
	@echo "Code generation completed"
