# Archbuild Agent Guidance

This document provides guidance for AI agents maintaining or extending the Archbuild codebase.

## Architecture Highlights

Archbuild is a modern Python rewrite of a legacy Bash-based Arch Linux package autobuilder. It prioritizes reliability, maintainability, and extensibility.

- **Asynchronous I/O**: Uses `asyncio` and `aiohttp` for AUR RPC queries and network-bound tasks.
- **Process Isolation**: Uses `ProcessPoolExecutor` for long-running `makepkg` builds to avoid GIL limitations.
- **Robust Locking**: Uses `fcntl.flock()` for file-based locking of the repository database and package builds.
- **Configuration**: Uses Pydantic V2 for YAML configuration loading and validation.
- **Logging**: Uses `rich` for structured, colorful terminal logging and optional file logging.

## Core Modules

- `cli.py`: Command-line interface using `click`.
- `config.py`: Configuration models and migration tools.
- `aur.py`: AUR RPC API client with caching and retry logic.
- `resolver.py`: Dependency resolution and topological sorting.
- `builder.py`: Build orchestration and parallelization.
- `repo.py`: Repository database and package retention management.
- `notifications.py`: Extensible notification system (Email, Webhooks).

## Development Patterns

### Async Usage
Always use async context managers for `AURClient` and `Builder` to ensure sessions and executors are properly closed.

```python
async with AURClient() as aur:
    async with Builder(config, aur) as builder:
        await builder.build_all()
```

### Locking
Always wrap repository updates in the repository lock found in `RepoManager`.

### Adding New Commands
Add new commands to `cli.py` using Click. If a command needs AUR access or building, use the `AURClient` and `Builder` inside an async function wrapped by `run_async`.

### Testing
- Unit tests are in `tests/test_*.py`.
- Integration tests are in `tests/integration_test.py` and require an Arch Linux environment.
- Use `pytest` for running unit tests.

## Maintenance Checklist

- [ ] Ensure all Pydantic models in `config.py` are up to date with new features.
- [ ] Maintain the topological sort logic in `resolver.py` (Kahn's algorithm).
- [ ] Keep `DLAGENTS` and `VCSCLIENTS` in `integration_test.py` in sync with system defaults.
- [ ] Update `README.md` when adding new CLI commands or configuration options.
