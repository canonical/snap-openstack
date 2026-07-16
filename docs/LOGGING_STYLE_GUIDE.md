# Sunbeam Logging Style Guide

## Overview

The Sunbeam codebase enforces logging conventions through two mechanisms:

1. **Automated linting** — Ruff rules `LOG` ([flake8-logging](https://docs.astral.sh/ruff/rules/#flake8-logging-log)) and `G` ([flake8-logging-format](https://docs.astral.sh/ruff/rules/#flake8-logging-format-g)) catch formatting violations at CI time.
2. **Style conventions** — This document covers semantic and stylistic rules that linters cannot enforce. These were established during the logging standardization effort (PR #740) and should be followed for all new code.

Both mechanisms work together: the linter prevents mechanical mistakes (f-strings in log calls, deprecated APIs), while these conventions ensure logs are **descriptive**, **non-redundant**, and **traceback-appropriate**.

## What the Linter Catches

The `LOG` and `G` rule groups are enabled in `sunbeam-python/pyproject.toml`:

```toml
[tool.ruff.lint]
select = [
    # ... other rules ...
    "LOG", # flake8-logging
    "G",   # flake8-logging-format
]
```

### `LOG` — flake8-logging

| Rule | Description |
|------|-------------|
| `LOG001` | Direct use of `logging.WARN` (use `WARNING`) |
| `LOG002` | Use of `__cached__` as logger name |
| `LOG009` | Use of `logging.WARN()` method |
| `LOG015` | `LOG.exception()` outside of an exception handler |

### `G` — flake8-logging-format

| Rule | Description |
|------|-------------|
| `G001` | Logging uses `str.format()` — use lazy `%`-style args |
| `G002` | Logging uses `%` operator for inline formatting |
| `G003` | Logging uses `+` string concatenation |
| `G004` | Logging uses f-string — use lazy `%`-style args |
| `G010` | `logging.warn()` is deprecated, use `logging.warning()` |
| `G101` | Extra keys conflict with `LogRecord` attributes |
| `G201` | `LOG.exception()` with redundant `exc_info` set |
| `G202` | `LOG.exception()` with `exc_info=False` (use `LOG.error`) |

### Running the Linter

```bash
# Full lint check (includes LOG and G rules)
tox -e pep8

# Check specific rules only
.tox/pep8/bin/ruff check --select LOG,G sunbeam-python/

# Check a specific file
.tox/pep8/bin/ruff check --select LOG,G sunbeam-python/sunbeam/steps/juju.py
```

## What the Linter Does NOT Catch

The following conventions must be enforced through code review. Each section below describes a rule, explains why it matters, and shows before/after examples from the codebase.

### 1. Use `LOG.exception()` Only for Terminal Errors

`LOG.exception()` automatically includes a full traceback. Only use it when the error **stops execution** — i.e., the exception is re-raised or causes a program exit.

**Why:** Tracebacks in logs for handled errors create noise, make real errors harder to find, and alarm operators unnecessarily.

**Correct usage — error re-raises, traceback is valuable:**

```python
# sunbeam/core/terraform.py
except subprocess.CalledProcessError as e:
    LOG.exception("terraform init failed: %s", e.stderr)
    raise TerraformException(str(e))
```

```python
# sunbeam/commands/launch.py
except ValueError as e:
    LOG.exception("Error resolving management ip from cidr")
    raise click.ClickException(str(e)) from e
```

**Incorrect usage — error is handled gracefully, traceback is noise:**

```python
# BAD
except TerraformException as e:
    LOG.exception("Error configuring cloud")
    return Result(ResultType.FAILED, str(e))

# GOOD — use LOG.warning instead
except TerraformException as e:
    LOG.warning("Error configuring cloud: %r", e)
    return Result(ResultType.FAILED, str(e))
```

**Rule of thumb:** If the next line after the `LOG` call is `return Result(ResultType.FAILED, ...)`, use `LOG.warning`. If the next line is `raise`, use `LOG.exception`.

### 2. Do Not Pass Bare Exceptions or `str(e)` as the Log Message

Bare exception objects or `str(e)` produce uninformative log lines with no context about what operation failed. Always include a descriptive message and use `%r` for exceptions.

**Why:** A log line like `"Connection refused"` gives no indication of which connection, to what, or during what operation. Descriptive messages make debugging significantly faster.

```python
# BAD — no context
LOG.debug(e)
LOG.warning(str(e))
LOG.debug(e.stderr)

# GOOD — descriptive context
LOG.debug("Failed to add manifest to cluster db: %r", e)
LOG.warning("Timed out waiting for certificates application: %r", e)
LOG.debug("Subprocess failed: %s: %s", e, e.stderr)
```

**Real codebase examples:**

```python
# sunbeam/steps/juju.py — controller not found
except ControllerNotFoundException as e:
    LOG.debug("Controller %s is not found: %r", self.controller, e)

# sunbeam/commands/configure.py — terraform error
except TerraformException as e:
    LOG.warning("Error getting Terraform output: %r", e)
    return Result(ResultType.FAILED, str(e))

# sunbeam/utils.py — connection check
except Exception as e:
    LOG.debug("Not able to connect to %s:%s server: %r", ip, port, e)
```

### 3. Avoid Redundant Log Calls for the Same Error

Do not log the same exception at multiple levels. Pick one appropriate level with a single, descriptive message.

**Why:** Duplicate log entries for the same error create confusion about whether multiple errors occurred.

```python
# BAD — two log calls for one error
except subprocess.CalledProcessError as e:
    LOG.exception("Error bootstrapping Juju")
    LOG.warning("%s: %s", e, e.stderr)
    return Result(ResultType.FAILED, str(e))

# GOOD — single descriptive message
except subprocess.CalledProcessError as e:
    LOG.warning("Error bootstrapping Juju: %s: %s", e, e.stderr)
    return Result(ResultType.FAILED, str(e))
```

**Real codebase example:**

```python
# sunbeam/steps/juju.py — handled error with context
except subprocess.CalledProcessError as e:
    LOG.warning(
        "Error determining whether to skip the bootstrap "
        "process. Defaulting to not skip: %s: %s",
        e,
        e.stderr,
    )
    return Result(ResultType.FAILED, str(e))
```

### 4. Use `%r` for Exceptions, `%s` for Human-Readable Values

- **`%r`** — use for exceptions and objects where the repr (including type name) is useful
- **`%s`** — use for strings, names, IPs, and other human-readable values

**Why:** `%r` shows the exception type alongside the message (e.g., `ConnectionError('refused')` vs just `refused`), which is critical for debugging. `%s` is cleaner for values that are already human-readable.

```python
# GOOD — %r for exception, %s for names
LOG.debug("Controller %s is not found: %r", self.controller, e)
LOG.warning("Timed out waiting for %s to be ready: %r", app_name, e)
LOG.debug("Running command %s", " ".join(cmd))
LOG.debug("Unit %s is deployed on machine: %s", name, self.machine_id)
```

**Corollary:** Use `%r` instead of `'%s'` when quoting variable values:

```python
# BAD
LOG.debug("Got value '%s' for key '%s'", value, key)

# GOOD
LOG.debug("Got value %r for key %r", value, key)
```

### 5. Use Consistent, Descriptive Messages

Log messages should clearly describe **what happened** and include relevant context (what entity, what operation).

**Why:** Vague messages like `"Error occurred"` require digging through code to understand what they mean. Descriptive messages make logs self-documenting.

```python
# BAD — vague or inconsistent
LOG.debug("Demo setup not yet done")
LOG.debug("Error: Terraform state locked")
LOG.debug("Cluster already bootstrapped")

# GOOD — precise and descriptive
LOG.debug("Demo setup is not yet done")
LOG.debug("Terraform state locked")
LOG.debug("Cluster is already bootstrapped")
```

#### Standard Message Patterns

Use these patterns consistently across the codebase:

| Pattern | Example |
|---------|---------|
| Entity not found | `LOG.debug("Model %s is not found: %r", model, e)` |
| Timeout waiting | `LOG.warning("Timed out waiting for %s: %r", app, e)` |
| Operation failed | `LOG.warning("Failed to create Watcher audit: %r", e)` |
| Command execution | `LOG.debug("Running command %s", " ".join(cmd))` |
| Command result | `LOG.debug("Command finished. stdout=%r, stderr=%r", out, err)` |
| Subprocess error | `LOG.warning("Error bootstrapping Juju: %s: %s", e, e.stderr)` |
| State description | `LOG.debug("Cluster is already bootstrapped")` |
| Connection check | `LOG.debug("Not able to connect to %s:%s server: %r", ip, port, e)` |

### 6. Do Not Include Trailing Periods in Log Messages

```python
# BAD
LOG.debug("DPDK disabled.")
LOG.debug("Determining DPDK candidate interfaces.")

# GOOD
LOG.debug("DPDK disabled")
LOG.debug("Determining DPDK candidate interfaces")
```

### 7. Use Proper Casing for Product Names

Product and project names should use their official casing.

| Correct | Incorrect |
|---------|-----------|
| Juju | juju |
| MySQL | Mysql |
| Terraform | terraform (in messages) |
| OpenStack | Openstack |
| MicroCeph | microceph |
| MicroOVN | microovn |

```python
# BAD
LOG.warning("Error getting users list from juju.")

# GOOD
LOG.warning("Error getting users list from Juju")
```

### 8. Use `exc_info=True` Sparingly on `LOG.debug`

`exc_info=True` on `LOG.debug` is acceptable when:
- The error is expected/recoverable and you want the traceback only in debug logs
- The exception is caught but doesn't need user-visible logging

```python
# GOOD — expected failure, traceback only in debug
except ConfigItemNotFoundException:
    LOG.debug("Failed to pull state", exc_info=True)

# BAD — use %r instead when the traceback isn't needed
except Exception as e:
    LOG.debug(e, exc_info=True)

# GOOD
except Exception as e:
    LOG.debug("FQDN lookup failed: %r", e)
```

### 9. Choosing the Right Log Level

| Level | When to use | Example |
|-------|-------------|---------|
| `LOG.debug` | Internal state, command execution, expected/handled failures | `LOG.debug("Controller %s is not found: %r", name, e)` |
| `LOG.info` | User-relevant progress | `LOG.info("Applying DPDK configuration")` |
| `LOG.warning` | Handled errors that may affect behavior, timeouts, fallbacks | `LOG.warning("Error configuring cloud: %r", e)` |
| `LOG.error` | Errors shown to user at CLI level | `LOG.error("Error: %s", e)` |
| `LOG.exception` | **Only** when the error stops execution (re-raise or exit) | `LOG.exception("terraform init failed: %s", e.stderr)` |

**Real codebase example — using multiple levels correctly:**

```python
# sunbeam/utils.py — global error handler
try:
    return self.main(*args, **kwargs)
except SunbeamException as e:
    LOG.debug("SunbeamException caught: %r", e)    # debug: internal context
    LOG.error("Error: %s", e)                       # error: user-facing message
    sys.exit(1)
except Exception as e:
    LOG.debug("Unexpected exception caught: %r", e) # debug: internal context
    LOG.warning(message)                             # warning: troubleshooting hint
    LOG.error("Error: %s", e)                        # error: user-facing message
    sys.exit(1)
```

## Quick Reference

### Decision Tree: Which Log Level for Exceptions?

```
Exception caught
├── Re-raised or causes exit?
│   ├── Yes → LOG.exception("descriptive message: %s", e.detail)
│   │         raise NewException(...) from e
│   └── No (handled gracefully) →
│       ├── Expected/normal flow? → LOG.debug("context: %r", e)
│       └── May affect behavior?  → LOG.warning("context: %r", e)
│           return Result(ResultType.FAILED, str(e))
```

### Formatting Cheat Sheet

| Scenario | Format | Example |
|----------|--------|---------|
| Exception object | `%r` | `LOG.debug("Failed: %r", e)` |
| Exception + stderr | `%s: %s` | `LOG.warning("Error: %s: %s", e, e.stderr)` |
| String value | `%s` | `LOG.debug("Model %s found", name)` |
| Quoting a value | `%r` | `LOG.debug("Got value %r", value)` |
| Command | `%s` | `LOG.debug("Running command %s", " ".join(cmd))` |
| stdout/stderr output | `%r` | `LOG.debug("stdout=%r, stderr=%r", out, err)` |

### Error Logging Output Reference

Understanding what each format specifier and log method produces helps choose the right one.

**Standard exception:**

```python
try:
    raise ValueError("Bad thing") from Exception("original cause")
except ValueError as e:
    LOG.debug("Error with %s: %s", "format", e)
    LOG.debug("Error with %r: %r", "format", e)
    LOG.debug(e, exc_info=True)
    LOG.exception("Caught an exception")
```

```
DEBUG   Error with format: Bad thing
DEBUG   Error with format: ValueError('Bad thing')
DEBUG   Bad thing                              # bare e — no context, avoid this
Exception: original cause                      # exc_info=True adds full traceback
  ...
ValueError: Bad thing
ERROR   Caught an exception                    # LOG.exception — always ERROR + traceback
Exception: original cause
  ...
ValueError: Bad thing
```

**`subprocess.CalledProcessError`:**

```python
try:
    subprocess.run(["juju", "show-controller", "nonexistent"],
                   check=True, capture_output=True, text=True)
except subprocess.CalledProcessError as e:
    LOG.debug("e with %%s : %s", e)
    LOG.debug("e with %%r : %r", e)
    LOG.debug("Output    : %s", e.output)
    LOG.debug("Stderr    : %s", e.stderr)
    LOG.exception("Caught a CalledProcessError")
```

```
DEBUG   e with %s : Command '['juju', 'show-controller', 'nonexistent']' returned non-zero exit status 1.
DEBUG   e with %r : CalledProcessError(1, ['juju', 'show-controller', 'nonexistent'])
DEBUG   Output    :
DEBUG   Stderr    : ERROR controller nonexistent not found
ERROR   Caught a CalledProcessError
Traceback (most recent call last):
  ...
subprocess.CalledProcessError: Command '['juju', ...]' returned non-zero exit status 1.
```

**Key takeaways:**
- `%s` on an exception gives the human-readable message
- `%r` on an exception gives the type + message (e.g., `ValueError('Bad thing')`) — preferred for debugging
- `%s` on `e.stderr` gives the actual error output from the subprocess — often the most useful detail
- `LOG.exception` always logs at `ERROR` level with full traceback — reserve for terminal errors
- `LOG.debug(e, exc_info=True)` logs at `DEBUG` with traceback but uses bare exception as message — avoid, use a descriptive message instead

## Implementation Details

### Logger Initialization

All modules use a module-level logger:

```python
import logging

LOG = logging.getLogger(__name__)
```

### Files and Configuration

| File | Purpose |
|------|---------|
| `sunbeam-python/pyproject.toml` | Ruff configuration with `LOG` and `G` rules |
| `sunbeam-python/tox.ini` | `pep8` environment runs `ruff check` |
| `sunbeam-python/sunbeam/core/terraform.py` | Reference for correct `LOG.exception` usage (5 calls, all re-raise) |
| `sunbeam-python/sunbeam/utils.py` | Reference for global error handler patterns |
| `sunbeam-python/sunbeam/steps/juju.py` | Reference for subprocess error logging patterns |

## Code Review Checklist

When reviewing logging changes, check for:

- [ ] **`LOG.exception` only on re-raise/exit**: Does the exception handler re-raise or exit? If not, use `LOG.warning` or `LOG.debug`
- [ ] **No bare exceptions**: Is the log message descriptive? No `LOG.debug(e)` or `LOG.warning(str(e))`
- [ ] **No redundant calls**: Is the same error logged at multiple levels?
- [ ] **Correct format specifiers**: `%r` for exceptions, `%s` for human-readable strings
- [ ] **Descriptive messages**: Does the message explain what operation failed and on what entity?
- [ ] **No trailing periods**: Log messages should not end with `.`
- [ ] **Correct product names**: Juju, MySQL, Terraform, OpenStack (not juju, Mysql, etc.)
- [ ] **Appropriate level**: `debug` for internals, `warning` for handled errors, `exception` for terminal errors

## Troubleshooting

### Linter Passes but Style Is Wrong

The `LOG` and `G` ruff rules only check formatting mechanics (f-strings, `.format()`, concatenation, deprecated APIs). They do **not** check:
- Whether `LOG.exception` is appropriate for the error handler
- Whether the message is descriptive
- Whether the log level matches the severity
- Whether multiple calls log the same error

These must be caught during code review using this guide.

### Common Anti-Patterns

**Anti-pattern: `LOG.exception` + `return Result(FAILED)`**

This is the most common issue. If the error is handled and execution continues, the traceback is misleading.

```python
# BAD — traceback implies a crash, but execution continues normally
except TerraformException as e:
    LOG.exception("Error configuring cloud")
    return Result(ResultType.FAILED, str(e))
```

**Anti-pattern: `LOG.exception` + `LOG.warning` for the same error**

This produces two log entries for one error — the traceback from `LOG.exception` and a separate warning.

```python
# BAD — redundant logging
except subprocess.CalledProcessError as e:
    LOG.exception("Error running command")
    LOG.warning("%s: %s", e, e.stderr)
```

**Anti-pattern: bare exception as message**

This loses all context about what operation was being attempted.

```python
# BAD — which operation? which entity?
except Exception as e:
    LOG.debug(e)
```

## Contributing

When adding new logging calls:

1. Follow the patterns documented here
2. Use the decision tree to select the correct log level
3. Include descriptive context in all messages (what operation, what entity)
4. Run `tox -e pep8` to verify linter rules pass
5. Self-review against the code review checklist above
