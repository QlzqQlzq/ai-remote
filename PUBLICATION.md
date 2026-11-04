# Public Source Snapshot

This repository contains the general SSH CLI, package metadata, Windows SSH
setup helper and a repeatable integration test.

Server profiles, passwords, SSH keys, known-host databases, production reports,
chat records, database snapshots and one-time operational scripts are excluded.
Documentation uses example paths and the reserved example address 203.0.113.10.

Run checks with:

```powershell
python -m unittest discover -s tests -v
```

The test launches the real CLI against a disposable SSH/SFTP server bound to
127.0.0.1. It covers initial configuration, command success and failure, upload,
download, deploy, exclusions, missing inputs, authentication failure and changed
host-key rejection. The Windows administrator setup script and real production
deployment are not run by this test.

No repository-wide license has been selected.
