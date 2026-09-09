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

### From Source
```bash
# Clone the repository
git clone https://github.com/Chizi123/Arch-autobuild-repo.git
cd Arch-autobuild-repo

# Set up virtual environment and install
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Native Arch Linux Package
To build and install as a native system package:
```bash
makepkg -si
```

### Standalone Binary
To create a standalone executable that doesn't require Python:
```bash
python scripts/build_binary.py
```
The binary will be available at `dist/archrepobuild`.

## Quick Start

1. **Create configuration**:
   ```bash
   cp config/config.example.yaml config.yaml
   # Edit config.yaml with your settings
   ```

2. **Initialize repository**:
   ```bash
   archrepobuild -c config.yaml init
   ```

3. **Add packages**:
   ```bash
   archrepobuild add yay paru
   ```

4. **Build all packages**:
   ```bash
   archrepobuild build-all
   ```

5. **Build a specific package**:
   ```bash
   archrepobuild build <package>
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
  # Abort build-all below this much free space on the repo filesystem (GiB, 0 = disabled)
  min_free_space_gb: 2

retention:
  keep_versions: 3
  # Remove downloaded source archives from build dir (AppImages, tar.gz, deb, etc.)
  clean_sources: false

notifications:
  email:
    enabled: true
    to: "admin@example.com"
    # Warn (and email) below this much free space on the repo filesystem (GiB, 0 = disabled)
    disk_space_threshold_gb: 5
```

> **Note:** Build directories are never removed automatically — the `cleanup` command only prunes old repo versions, old build artifacts, and (optionally) stale source archives. To delete a package's build directory, use `archrepobuild remove <package>`.

## Migration from Bash Version

```bash
archrepobuild migrate-config vars.sh -o config.yaml
```

## Systemd Timer

Create `/etc/systemd/system/archrepobuild.service`:
```ini
[Unit]
Description=Build AUR packages

[Service]
Type=oneshot
ExecStart=/usr/bin/archrepobuild -c /etc/archrepobuild/config.yaml build-all
User=builduser
```

Create `/etc/systemd/system/archrepobuild.timer`:
```ini
[Unit]
Description=Run archrepobuild daily

[Timer]
OnCalendar=daily
Persistent=true

[Install]
WantedBy=timers.target
```

## License

MIT
