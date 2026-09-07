"""Restore the newest checkpoint artifact; never silently fall back to stale state."""
import os
from pathlib import Path
import tempfile
import zipfile

import requests


def restore():
    repo = os.environ["GITHUB_REPOSITORY"]
    session = requests.Session()
    session.headers.update({"Authorization": f"Bearer {os.environ['GH_TOKEN']}", "Accept": "application/vnd.github+json"})
    artifacts = []
    page = 1
    while not artifacts:
        r = session.get(f"https://api.github.com/repos/{repo}/actions/artifacts", params={"name": "influencer-checkpoint", "per_page": 100, "page": page}, timeout=30)
        r.raise_for_status()
        batch = r.json()["artifacts"]
        artifacts = [a for a in batch if a.get("workflow_run", {}).get("head_branch") == os.environ["GITHUB_REF_NAME"]]
        if len(batch) < 100:
            break
        page += 1
    if not artifacts:
        if Path("data/influencer/status.json").exists():
            raise RuntimeError("Checkpoint missing. Explicit reset required; refusing automatic re-query.")
        return
    artifact = artifacts[0]
    if artifact["expired"]:
        raise RuntimeError("Checkpoint expired. Explicit reset required.")
    with session.get(artifact["archive_download_url"], timeout=120, stream=True) as r, tempfile.TemporaryFile() as downloaded:
        r.raise_for_status()
        for chunk in r.iter_content(1024 * 1024):
            downloaded.write(chunk)
        downloaded.seek(0)
        extract(downloaded)


def extract(downloaded):
    with zipfile.ZipFile(downloaded) as archive:
        # Extract exactly one known filename; do not trust archive paths.
        with archive.open("checkpoint.sqlite") as src:
            target = Path(".influencer-state/checkpoint.sqlite")
            target.parent.mkdir(exist_ok=True)
            with target.open("wb") as out:
                while chunk := src.read(1024 * 1024):
                    out.write(chunk)


if __name__ == "__main__":
    try:
        restore()
    except Exception:
        print("Checkpoint restore failed. Refusing to start a fresh paid scan. Check artifact availability or explicitly reset.")
        raise SystemExit(1)
