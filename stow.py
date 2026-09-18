#!/usr/bin/env python3
"""Stow: packs Voyager/Ridgeback run logs away into a structured backup tree.

For each configured environment (a source directory of uuid run-folders plus a
Voyager jobs API server), this script looks up job metadata per uuid, copies
filtered .json/.log/.txt files into a structured backup directory, and saves
the full job JSON alongside them. Multiple environments can be backed up into
the same backup root, which can optionally be compressed at the end.
"""

import argparse
import getpass
import json
import logging
import os
import shutil
import tarfile
import uuid as uuid_module
from urllib.parse import urljoin, urlparse

import requests
from requests.auth import HTTPBasicAuth

logger = logging.getLogger("stow")

INCLUDED_EXTENSIONS = {".json", ".log", ".txt"}
EXCLUDED_EXTENSIONS = {".cwl"}


def is_valid_uuid(name):
    """Return True if name parses as a UUID (run-folder names are uuids)."""
    try:
        uuid_module.UUID(name)
        return True
    except (ValueError, AttributeError):
        return False


def fetch_job_json(base_url, job_uuid, username, password):
    """GET the job JSON from the Voyager API, or None on any failure."""
    url = urljoin(base_url.rstrip("/") + "/", f"v0/jobs/{job_uuid}/")
    try:
        response = requests.get(url, auth=HTTPBasicAuth(username, password), timeout=30)
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        # include response body/headers so auth-scheme mismatches are diagnosable
        detail = ""
        if exc.response is not None:
            www_auth = exc.response.headers.get("WWW-Authenticate")
            body = exc.response.text[:500]
            detail = f" | WWW-Authenticate={www_auth!r} | body={body!r}"
        logger.warning("Failed to fetch job JSON for %s from %s: %s%s", job_uuid, url, exc, detail)
        return None
    except ValueError as exc:
        logger.warning("Invalid JSON in response for %s from %s: %s", job_uuid, url, exc)
        return None


def extract_metadata(job_json):
    """Pull org, entrypoint basename, and version out of a job JSON, or None."""
    try:
        github = job_json["app"]["github"]
        repository = github["repository"]
        entrypoint = github["entrypoint"]
        version = github["version"]
    except (KeyError, TypeError) as exc:
        logger.warning("Job JSON missing required app.github fields: %s", exc)
        return None

    # org is the first path segment of the github repo url, e.g. msk-access/chip-var
    path_parts = [p for p in urlparse(repository).path.split("/") if p]
    if not path_parts:
        logger.warning("Could not parse org from repository url: %s", repository)
        return None
    org = path_parts[0]

    entrypoint_base = os.path.splitext(os.path.basename(entrypoint))[0]
    if not entrypoint_base:
        logger.warning("Could not parse entrypoint basename from: %s", entrypoint)
        return None

    return {"org": org, "entrypoint_base": entrypoint_base, "version": version}


def copy_filtered_files(source_uuid_dir, dest_files_dir):
    """Recursively copy .json/.log/.txt files (no symlinks/.cwl), preserving structure."""
    copied = 0
    for root, _dirs, files in os.walk(source_uuid_dir):
        for filename in files:
            src_path = os.path.join(root, filename)
            if os.path.islink(src_path):
                continue
            ext = os.path.splitext(filename)[1].lower()
            if ext in EXCLUDED_EXTENSIONS or ext not in INCLUDED_EXTENSIONS:
                continue
            rel_path = os.path.relpath(src_path, source_uuid_dir)
            dest_path = os.path.join(dest_files_dir, rel_path)
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            shutil.copy2(src_path, dest_path)
            copied += 1
    return copied


def backup_uuid_folder(source_uuid_dir, job_uuid, job_json, metadata, backup_root, env_label):
    """Write the filtered files and run.json for one uuid into the backup tree."""
    dest_dir = os.path.join(
        backup_root,
        env_label,
        metadata["org"],
        metadata["entrypoint_base"],
        str(metadata["version"]),
        job_uuid,
    )
    dest_files_dir = os.path.join(dest_dir, "files")
    os.makedirs(dest_files_dir, exist_ok=True)

    copied = copy_filtered_files(source_uuid_dir, dest_files_dir)

    run_json_path = os.path.join(dest_dir, "run.json")
    with open(run_json_path, "w", encoding="utf-8") as f:
        json.dump(job_json, f, indent=2)

    logger.info("Backed up %s -> %s (%d files)", job_uuid, dest_dir, copied)


def run_env(config, backup_root):
    """Back up every uuid folder in config['source_dir'] into backup_root."""
    source_dir = config["source_dir"]
    entries = sorted(os.listdir(source_dir))

    succeeded = 0
    skipped = 0
    for name in entries:
        entry_path = os.path.join(source_dir, name)
        if not os.path.isdir(entry_path) or not is_valid_uuid(name):
            continue

        job_json = fetch_job_json(config["base_url"], name, config["username"], config["password"])
        if job_json is None:
            skipped += 1
            continue

        metadata = extract_metadata(job_json)
        if metadata is None:
            skipped += 1
            continue

        try:
            backup_uuid_folder(entry_path, name, job_json, metadata, backup_root, config["env_label"])
            succeeded += 1
        except OSError as exc:
            logger.warning("Failed to back up %s: %s", name, exc)
            skipped += 1

    logger.info(
        "Environment '%s' complete: %d backed up, %d skipped", config["env_label"], succeeded, skipped
    )


def parse_args():
    parser = argparse.ArgumentParser(description="Back up Voyager/Ridgeback run logs into a structured tree.")
    parser.add_argument("--backup-root", required=True, help="Destination directory for all backups.")
    parser.add_argument(
        "--label",
        action="append",
        required=True,
        help="A label for an environment to back up (e.g. stage/prod). Repeatable.",
    )
    parser.add_argument(
        "--source-dir",
        action="append",
        required=True,
        help="Directory containing uuid run folders, paired positionally with --label. Repeatable.",
    )
    parser.add_argument(
        "--base-url",
        action="append",
        required=True,
        help="Server base URL (e.g. http://voyager:5003/), paired positionally with --label. Repeatable.",
    )
    parser.add_argument("--compress", action="store_true", help="Compress backup-root into a .tar.gz when done.")
    parser.add_argument(
        "--archive-name",
        default="backup",
        help="Base filename (without extension) for the compressed archive. Defaults to 'backup'.",
    )
    return parser.parse_args()


def prompt_credentials():
    """Ask for the username/password used to authenticate to an environment's API."""
    username = input("Enter the username: ").strip()
    password = getpass.getpass("Enter the password: ")
    return username, password


def create_archive(backup_dir):
    """Archive backup_dir beside itself, preserving its directory name as the tar root."""
    archive_parent = os.path.dirname(backup_dir)
    archive_name = os.path.basename(backup_dir)
    archive_path = os.path.join(archive_parent, f"{archive_name}.tar.gz")

    with tarfile.open(archive_path, "w:gz") as archive:
        archive.add(backup_dir, arcname=archive_name)
    return archive_path


def setup_logging(backup_root):
    log_path = os.path.join(backup_root, "backup.log")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)


def main():
    args = parse_args()
    if not (len(args.label) == len(args.source_dir) == len(args.base_url)):
        raise SystemExit("--label, --source-dir, and --base-url must be given the same number of times.")

    backup_root = os.path.abspath(args.backup_root)
    backup_dir = os.path.join(backup_root, args.archive_name)
    os.makedirs(backup_dir, exist_ok=True)
    setup_logging(backup_dir)

    configs = []
    for env_label, source_dir, base_url in zip(args.label, args.source_dir, args.base_url):
        logger.info("Credentials for environment '%s' (%s)", env_label, base_url)
        username, password = prompt_credentials()
        configs.append(
            {
                "source_dir": source_dir,
                "base_url": base_url,
                "username": username,
                "password": password,
                "env_label": env_label,
            }
        )

    for config in configs:
        run_env(config, backup_dir)

    if args.compress:
        archive_path = create_archive(backup_dir)
        logger.info("Created archive: %s", archive_path)


if __name__ == "__main__":
    main()
