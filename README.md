# Archbuild

A modern, sustainable AUR package building and repository management tool for Arch Linux.

## Features

- **Automatic AUR builds** - Clone and build packages from the AUR
- **Dependency resolution** - Automatically resolve and build AUR dependencies in correct order
- **Parallel builds** - Build multiple packages concurrently with configurable worker count
- **Package signing** - Optional GPG signing for packages and database
- **Retry logic** - Configurable retries with exponential backoff for transient failures
- **Package retention** - Keep configurable number of old versions, auto-cleanup
- **Notifications** - Email and webhook (Discord, Slack, ntfy) notifications on failures
- **VCS package handling** - Automatically rebuild git/svn/hg packages
- **Modern Python** - Type-safe, async, well-tested codebase

## Installation

```bash
# From source
pip install -e .

# Or with development dependencies
pip install -e ".[dev]"
```

## Quick Start

1. **Create configuration**:
   ```bash
   cp config/config.example.yaml config.yaml
   # Edit config.yaml with your settings
   ```

2. **Initialize repository**:
   ```bash
   archbuild -c config.yaml init
   ```

3. **Add packages**:
   ```bash
   archbuild add yay paru
   ```

4. **Build all packages**:
   ```bash
   archbuild build-all
   ```

5. **Build a specific package**:
   ```bash
   archbuild build <package>
   ```

## Commands

| Command                    | Description                                |
|----------------------------|--------------------------------------------|
| `init`                     | Initialize repository directories          |
| `add <packages...>`        | Add and build new packages                 |
| `remove <packages...>`     | Remove packages from repo                  |
| `build-all [-f]`           | Build all packages, `-f` forces rebuild    |
| `build <package>`          | Build a specific package                   |
| `check`                    | Check for packages moved to official repos |
| `list`                     | List packages in repository                |
| `remake`                   | Rebuild repository database                |
| `cleanup`                  | Remove old package versions                |
| `migrate-config <vars.sh>` | Migrate legacy config                      |
| `test-notifications`       | Test notification setup                    |

## Configuration

See `config/config.example.yaml` for all options. Key settings:

```yaml
repository:
  name: "myrepo"
  path: "/repo/x86_64"
  build_dir: "/repo/build"

building:
  parallel: true
  max_workers: 4
  retry_attempts: 3

retention:
  keep_versions: 3

notifications:
  email:
    enabled: true
    to: "admin@example.com"
```

## Migration from Bash Version

```bash
archbuild migrate-config vars.sh -o config.yaml
```

## Systemd Timer

Create `/etc/systemd/system/archbuild.service`:
```ini
[Unit]
Description=Build AUR packages

[Service]
Type=oneshot
ExecStart=/usr/bin/archbuild -c /etc/archbuild/config.yaml build-all
User=builduser
```

Create `/etc/systemd/system/archbuild.timer`:
```ini
[Unit]
Description=Run archbuild daily

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

## License

MIT
