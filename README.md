# Stow

To stow something is to pack it away carefully for storage — this script does
the same for **Voyager**/**Ridgeback** pipeline run logs.

`stow.py` walks a directory of run folders (named by job uuid), looks up each
job's metadata from the Voyager jobs API, and copies the relevant `.json`/`.log`/`.txt`
files into a clean, organized backup tree — keyed by org, pipeline, version, and uuid.

## What it does

For each uuid folder found in a source directory:

1. Looks up the uuid in the Ridgeback API: `{server_url}/v0/jobs/{uuid}/` (HTTP Basic Auth) to get the job's JSON.
2. Extracts the GitHub org, entrypoint name, and version from the job JSON.
3. Copies `.json`, `.log`, and `.txt` files from the run folder into the backup tree,
   preserving relative directory structure. Symlinks and `.cwl` files are skipped.
4. Saves the full job JSON as `run.json` alongside the copied files.

You can back up multiple source directories / servers ("environments", e.g. `stage`
and `prod`) into the same backup root in one session, and optionally compress the
whole backup root into a single `.tar.gz` archive at the end.

## Backup layout

```
{backup_root}/
  {env_label}/
    {org}/
      {entrypoint_name}/
        {version}/
          {uuid}/
            run.json
            files/
              ...   (relative paths preserved from the source run folder)
```

## Requirements

- Python 3.9+
- `requests` (see `requirements.txt`)

## Setup

```bash
pip install -r requirements.txt
```

## Usage

```bash
python3 stow.py \
  --backup-root /path/to/backups \
  --label stage --source-dir /path/to/stage/runs --base-url http://voyager:5003/ \
  --label prod --source-dir /path/to/prod/runs --base-url http://voyager-prod:5003/ \
  --compress
```

Arguments:

- `--backup-root` — destination directory for all backups (created if missing).
- `--label` — a label for an environment to back up (e.g. `stage`/`prod`). Repeatable.
- `--source-dir` — directory containing uuid run folders, paired positionally with `--label`. Repeatable.
- `--base-url` — server base URL (e.g. `http://voyager:5003/`), paired positionally with `--label`. Repeatable.

`--label`, `--source-dir`, and `--base-url` must each be given the same number of times — the
Nth occurrence of each forms one environment, and all environments are backed up into the same
`--backup-root`.

- `--compress` — optional flag to compress the entire backup root into a `.tar.gz` archive
  once all environments are done.
- `--archive-name` — base filename (without extension) for the compressed archive. Defaults
  to `backup` (i.e. produces `backup.tar.gz` next to `--backup-root`). The archive contents are rooted
  at a directory with this name. Only used with `--compress`.

Before any backup starts, you'll be prompted interactively for the **username** and **password**
(password input is hidden) for each listed environment.

A log of the run is written to both the console and `{backup_root}/backup.log`.

## Notes

- Only local run folders whose name is a valid UUID are processed; others are skipped.
- If a job lookup fails (network error, bad credentials, missing fields) that uuid is
  logged as a warning and skipped — the script keeps going.
