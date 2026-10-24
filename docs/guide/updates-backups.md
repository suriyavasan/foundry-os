# Updates and backups

Your company is only as safe as your last backup. Take one before anything risky.

## Where your data lives

Everything is in one folder: the database, your encrypted keys, files the agents made, skills and agent homes.

| How you installed | Data folder |
|---|---|
| From source (today) | `data/` inside the project folder |
| Homebrew or installer, macOS *(v0.1.0)* | `~/Library/Application Support/Foundry AI OS` |
| Homebrew or installer, Linux *(v0.1.0)* | `~/.local/share/foundry-os` (or `$XDG_DATA_HOME/foundry-os`) |
| Docker *(v0.1.0)* | The `foundry-os` volume mounted at `/data` |

## Back up

```bash
companyos backup create                       # saved in <data folder>/backups/
companyos backup create --out /Volumes/Safe/  # or choose where
```

What you get is one `.tar.gz` file with:

- a consistent copy of the database (safe to take while Foundry is running),
- your secret key and owner token, the agents' files and the skill library.

It does **not** include temporary work folders, downloaded helper programs or earlier backups.

**Guard that file.** It contains the key that unlocks your stored credentials, and the app does not encrypt it. The file is created readable only by you. If you copy it somewhere, put it on an encrypted disk or encrypt it yourself (for example with `age` or `gpg`).

PostgreSQL installs are not covered by this command. Use `pg_dump` and copy the key files yourself. (PostgreSQL support has not been tested.)

## Check a backup

```bash
companyos backup restore ARCHIVE --verify-only
```

This checks every file against its checksum and changes nothing. Do it now and then, because an untested backup is a hope, not a backup.

## Restore

Stop Foundry first.

```bash
companyos backup restore ARCHIVE            # into an empty or new data folder
companyos backup restore ARCHIVE --force    # replace an existing install (asks first)
```

Restore is cautious. It checks the whole archive before touching anything, and refuses if the file is damaged or edited, if Foundry is still running on that folder, or if the backup came from a newer version than the one you run. With `--force`, your current data is **moved aside** to a folder named `...pre-restore-<time>`, not deleted. Delete it yourself once you are happy.

## Updating

### Homebrew *(v0.1.0)*

```bash
brew upgrade foundry-os
brew services restart foundry-os      # if you use it as a background service
```

`foundryos update` will notice a Homebrew install and tell you to run the command above.

### One-line installer *(v0.1.0)*

```bash
foundryos update                      # or: foundryos update --version X
foundryos rollback                    # go back to the previous version
```

The update downloads the release for your computer, checks its SHA-256 checksum against the one published with that release, and refuses if it does not match. It keeps the previous version so `rollback` works. Foundry checks for a new release at most once a day and shows a banner. That check is a single request to GitHub; there is no other tracking, and you can turn it off.

### Automatic safety backup *(v0.1.0)*

Every version change takes a backup first, saved as `backups/pre-<version>-<time>.tar.gz` (the last 5 are kept). If a database upgrade fails, Foundry does not start and prints how to go back.

### Docker *(v0.1.0, not tested by us)*

Pull the new image, remove the old container and start a new one with the same `-v foundry-os:/data`. The volume keeps your data.

```bash
docker pull ghcr.io/suriyavasan/foundry-os:latest
docker rm -f foundry-os
docker run -d --name foundry-os -p 8700:8700 -v foundry-os:/data ghcr.io/suriyavasan/foundry-os:latest
```

### From source (today)

```bash
companyos backup create               # always first
git pull
cd backend && uv sync
uv run companyos up                   # upgrades the database and rebuilds a stale screen
```

Database upgrades only go forward. To go back, restore the backup you made before updating.

## Uninstalling

Removing the program keeps your data unless you ask otherwise (`foundryos uninstall --purge` asks you to confirm; `brew uninstall` always keeps it). Delete the data folder yourself only when you are sure you have a backup.
