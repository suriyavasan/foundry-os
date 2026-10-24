# Updates and backups

Your company is only as safe as your last backup. Take one before anything risky.

## Where your data lives

Everything is in one folder: the database, your encrypted keys, files the agents made, skills and agent homes.

| How you installed | Data folder |
|---|---|
| From source | `data/` inside the project folder |
| Homebrew or installer, macOS | `~/Library/Application Support/Foundry AI OS` |
| Homebrew or installer, Linux | `~/.local/share/foundry-os` (or `$XDG_DATA_HOME/foundry-os`) |
| Docker | The `foundry-os` volume mounted at `/data` |

`foundryos version --json` prints the exact folder your copy uses. `FOUNDRYOS_DATA_DIR` moves it (set it before the first start, and move the folder yourself if you already have data).

## Back up

```bash
foundryos backup create                       # saved in <data folder>/backups/
foundryos backup create --out /Volumes/Safe/  # or choose where
```

What you get is one `.tar.gz` file with:

- a consistent copy of the database (safe to take while Foundry is running),
- your secret key and owner token, the agents' files and the skill library.

It does **not** include temporary work folders, downloaded helper programs or earlier backups.

**Guard that file.** It contains the key that unlocks your stored credentials, and the app does not encrypt it. The file is created readable only by you. If you copy it somewhere, put it on an encrypted disk or encrypt it yourself (for example with `age` or `gpg`).

PostgreSQL installs are not covered by this command. Use `pg_dump` and copy the key files yourself. (PostgreSQL support has not been tested.)

## Check a backup

```bash
foundryos backup restore ARCHIVE --verify-only
```

This checks every file against its checksum and changes nothing. Do it now and then, because an untested backup is a hope, not a backup.

## Restore

Stop Foundry first.

```bash
foundryos backup restore ARCHIVE            # into an empty or new data folder
foundryos backup restore ARCHIVE --force    # replace an existing install (asks first)
```

Restore is cautious. It checks the whole archive before touching anything, and refuses if the file is damaged or edited, if Foundry is still running on that folder, or if the backup came from a newer version than the one you run. With `--force`, your current data is **moved aside** to a folder named `...pre-restore-<time>`, not deleted. Delete it yourself once you are happy.

## Updating

`foundryos update` knows which kind of install you have and does the right thing:

| You installed with | `foundryos update` |
|---|---|
| The one-line installer | Updates itself (below) |
| Homebrew | Prints `brew upgrade foundry-os && brew services restart foundry-os` and changes nothing |
| From source | Prints the `git pull` steps and changes nothing |
| Docker | Prints the `docker pull` steps and changes nothing |

### Homebrew

```bash
brew upgrade foundry-os
brew services restart foundry-os      # if you use it as a background service
```

### One-line installer

```bash
foundryos update                      # the latest release
foundryos update --version 0.2.0      # a specific one
foundryos update --check              # only look: is there something newer?
foundryos rollback                    # go back to the previous version
```

What `update` does, in order, and why it is safe:

1. looks up the release on GitHub and finds the download for your computer (for example `darwin-arm64`);
2. downloads it and the release's `SHA256SUMS` file, and **refuses** if the file is missing from that list, if there is no `SHA256SUMS`, or if the checksum does not match (nothing is unpacked in that case);
3. unpacks it next to the current version (`~/.foundry-os/versions/<version>`) and checks that it is a complete bundle of the version it claims to be;
4. switches the `current` link to it in one atomic step, so there is never a moment with no working version;
5. if Foundry AI OS was running, restarts it (through its background service if you have one) and says which version came up. If it does not start, the message tells you to run `foundryos rollback`;
6. keeps the previous version on disk (older ones are removed).

`foundryos rollback` switches `current` back to the previous version and restarts. If the update had upgraded your database in a way the old version cannot read, `rollback` stops and says so without changing anything; then `foundryos rollback --restore-data` also puts your data back as it was just before that update (using the automatic backup below; whatever happened since is moved aside to a `...pre-restore-<time>` folder, never deleted). Running `rollback` twice goes forward again.

### New-release notice

Foundry AI OS looks for a newer release at most once a day. That is one request to GitHub's public releases page, with a user agent like `foundry-os/0.1.0`; nothing else is sent and there is no other tracking. If there is one, you see a small notice at the top of the web app (only the owner sees it; you can dismiss it, or turn the check off from there), a line in `foundryos status`, `foundryos doctor` and when you start it, and the exact command to run for your kind of install.

Turn the check off: `foundryos update --no-auto-check` (on again with `--auto-check`), or set `FOUNDRYOS_UPDATE_CHECK=0`. `foundryos update --check` always asks, because you asked.

### Automatic safety backup

Whenever the version changed since the last start (or there are database upgrades waiting), Foundry AI OS takes a backup **before** it touches the database: `<data folder>/backups/pre-<new version>-<time>.tar.gz`. The five newest of these are kept; backups you make yourself are never deleted. It is the same checksummed backup as `foundryos backup create`.

If the backup cannot be made (for example the disk is full), or the database upgrade fails, Foundry AI OS **does not start** and prints what to do: which command goes back to the old version and the `foundryos backup restore "<that file>" --force` command that puts your data back. If it finds data written by a *newer* version than the one you just started (after a manual downgrade), it refuses to touch it too. Set `FOUNDRYOS_SKIP_PRE_UPGRADE_BACKUP=1` only if you know why you need to skip the backup.

### Docker

Pull the new image, remove the old container and start a new one with the same `-v foundry-os:/data`. The volume keeps your data.

```bash
docker pull ghcr.io/suriyavasan/foundry-os:latest
docker rm -f foundry-os
docker run -d --name foundry-os -p 8700:8700 -v foundry-os:/data ghcr.io/suriyavasan/foundry-os:latest
```

### From source

```bash
foundryos backup create               # optional: the start below also takes one
git pull
cd backend && uv sync
uv run foundryos restart              # upgrades the database (after an automatic backup) and rebuilds a stale screen
```

Database upgrades only go forward. To go back, restore the backup you made before updating.

## Uninstalling

```bash
foundryos uninstall                   # stops it, removes the background service and, for the installer, the program
foundryos uninstall --purge           # also deletes your data folder, after you type "delete my data"
foundryos uninstall --purge --yes-delete-my-data   # the same without the question, for scripts
```

Your data is kept unless you add `--purge`. `--purge` refuses any folder that does not look like Foundry AI OS data (your home folder, `/`, a folder of photos) and removes nothing else if it refuses. For a Homebrew install, `foundryos uninstall` never touches what Homebrew owns: it stops the program and tells you to run `brew services stop foundry-os` and `brew uninstall foundry-os` (which also leaves your data). A source checkout has nothing to uninstall.
