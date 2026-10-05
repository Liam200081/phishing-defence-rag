"""Explicit, opt-in fetch of pinned public corpus files. Never called by tools/tests."""
import hashlib
import io
import json
import ssl
import subprocess
import tarfile
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "data" / "m2_manifest.json"


def download(url, timeout):
    try:
        with urlopen(url, timeout=timeout) as response:
            return response.read()
    except URLError as exc:
        if not isinstance(exc.reason, ssl.SSLCertVerificationError):
            raise
        # Some macOS Python installs lack the system CA bundle. curl uses the
        # system trust store; its TLS verification remains enabled.
        return subprocess.run(["curl", "--location", "--fail", "--silent", "--show-error",
                               "--max-time", str(timeout), url], check=True,
                              capture_output=True).stdout


def main():
    manifest = json.loads(MANIFEST.read_text())
    archives = {}
    for sample in manifest["samples"]:
        source = manifest["sources"][sample["source_id"]]
        if source["kind"] == "github_raw":
            url = source["base_url"] + "/" + sample["upstream_path"]
            content = download(url, 30)
        else:
            if sample["source_id"] not in archives:
                archive = download(source["archive_url"], 60)
                if hashlib.sha256(archive).hexdigest() != source["archive_sha256"]:
                    raise ValueError("archive hash mismatch")
                archives[sample["source_id"]] = archive
            with tarfile.open(fileobj=io.BytesIO(archives[sample["source_id"]]), mode="r:bz2") as tar:
                content = tar.extractfile(sample["upstream_path"]).read()
        if hashlib.sha256(content).hexdigest() != sample["sha256"]:
            raise ValueError(f"sample hash mismatch: {sample['sample_id']}")
        target = ROOT / sample["local_path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        print(sample["sample_id"], target)


if __name__ == "__main__":
    main()
