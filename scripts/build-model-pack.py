#!/usr/bin/env python3
"""Download a portable, verified Sammie-Roto 2 model pack.

This script uses only the Python standard library. It reads MODEL_REGISTRY from
the application source with ``ast`` instead of importing PySide6 and requests.
Partial downloads use a ``.part`` suffix and resume when the server supports
HTTP range requests.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import time
from urllib.error import HTTPError
from urllib.parse import unquote, urlsplit
from urllib.request import Request, urlopen


CHUNK_SIZE = 8 * 1024 * 1024
WEIGHT_SUFFIXES = (".pt", ".pth", ".safetensors")


def digest(path: Path, algorithm: str) -> str:
    if algorithm == "md5":
        checksum = hashlib.md5(usedforsecurity=False)
    else:
        checksum = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(CHUNK_SIZE), b""):
            checksum.update(chunk)
    return checksum.hexdigest()


def read_registry(registry_path: Path) -> list[dict[str, str]]:
    tree = ast.parse(registry_path.read_text(encoding="utf-8"), registry_path)
    registry_node: ast.Dict | None = None

    for node in tree.body:
        if (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == "MODEL_REGISTRY"
            and isinstance(node.value, ast.Dict)
        ):
            registry_node = node.value
            break

    if registry_node is None:
        raise RuntimeError(f"MODEL_REGISTRY was not found in {registry_path}")

    models: list[dict[str, str]] = []
    for key_node, value_node in zip(registry_node.keys, registry_node.values):
        key = ast.literal_eval(key_node)
        if not isinstance(key, str) or not isinstance(value_node, ast.Call):
            raise RuntimeError("MODEL_REGISTRY has an unsupported entry")

        values = {
            keyword.arg: ast.literal_eval(keyword.value)
            for keyword in value_node.keywords
            if keyword.arg is not None
        }
        if not all(
            isinstance(values.get(name), str) for name in ("url", "md5", "dest_dir")
        ):
            raise RuntimeError(f"Model entry {key!r} is missing literal url/md5/dest_dir values")

        url = values["url"]
        destination_dir = Path(values["dest_dir"])
        if destination_dir.is_absolute() or ".." in destination_dir.parts:
            raise RuntimeError(f"Model entry {key!r} has an unsafe destination directory")
        models.append(
            {
                "key": key,
                "url": url,
                "md5": values["md5"].lower(),
                "dest_dir": values["dest_dir"],
                "filename": unquote(Path(urlsplit(url).path).name),
            }
        )

    return models


def copy_checkpoint_metadata(repo_root: Path, output_dir: Path) -> None:
    source_root = repo_root / "checkpoints"
    destination_root = output_dir / "checkpoints"
    for source in source_root.rglob("*"):
        if not source.is_file() or source.name.endswith(WEIGHT_SUFFIXES):
            continue
        destination = destination_root / source.relative_to(source_root)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def report_progress(filename: str, downloaded: int, total: int | None, started: float) -> None:
    elapsed = max(time.monotonic() - started, 0.001)
    mib = downloaded / (1024 * 1024)
    rate = mib / elapsed
    if total:
        percent = downloaded * 100 / total
        total_mib = total / (1024 * 1024)
        message = (
            f"\r  {filename}: {mib:,.0f}/{total_mib:,.0f} MiB "
            f"({percent:5.1f}%) at {rate:,.1f} MiB/s"
        )
    else:
        message = f"\r  {filename}: {mib:,.0f} MiB at {rate:,.1f} MiB/s"
    print(message, end="", flush=True)


def download(model: dict[str, str], output_dir: Path) -> Path:
    destination_dir = output_dir / model["dest_dir"]
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / model["filename"]
    partial = destination.with_name(destination.name + ".part")

    if destination.exists() and digest(destination, "md5") == model["md5"]:
        print(f"[cached] {model['key']}: {destination.relative_to(output_dir)}")
        return destination

    resume_at = partial.stat().st_size if partial.exists() else 0
    headers = {"User-Agent": "Sammie-Roto-2-offline-model-pack/1"}
    if resume_at:
        headers["Range"] = f"bytes={resume_at}-"
        print(f"[resume] {model['key']} from {resume_at / (1024 * 1024):,.0f} MiB")
    else:
        print(f"[download] {model['key']}")

    request = Request(model["url"], headers=headers)
    try:
        response = urlopen(request, timeout=60)
    except HTTPError as error:
        if error.code == 416 and partial.exists():
            if digest(partial, "md5") == model["md5"]:
                os.replace(partial, destination)
                print(f"[verified] {model['key']}")
                return destination
            partial.unlink()
            return download(model, output_dir)
        raise

    with response:
        resumed = resume_at > 0 and response.status == 206
        mode = "ab" if resumed else "wb"
        downloaded = resume_at if resumed else 0
        content_length = response.headers.get("Content-Length")
        total = downloaded + int(content_length) if content_length else None
        started = time.monotonic()
        next_report = downloaded

        with partial.open(mode) as stream:
            while True:
                chunk = response.read(CHUNK_SIZE)
                if not chunk:
                    break
                stream.write(chunk)
                downloaded += len(chunk)
                if downloaded >= next_report or (total and downloaded == total):
                    report_progress(model["filename"], downloaded, total, started)
                    next_report = downloaded + 128 * 1024 * 1024
        print()

    actual_md5 = digest(partial, "md5")
    if actual_md5 != model["md5"]:
        partial.unlink(missing_ok=True)
        raise RuntimeError(
            f"Checksum mismatch for {model['key']}: expected {model['md5']}, got {actual_md5}"
        )

    os.replace(partial, destination)
    print(f"[verified] {model['key']}: {destination.relative_to(output_dir)}")
    return destination


def write_manifests(repo_root: Path, output_dir: Path, models: list[dict[str, str]]) -> None:
    revision = "unknown"
    try:
        revision = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    info = output_dir / "MODEL-PACK-INFO.txt"
    info.write_text(
        "Sammie-Roto 2 portable model pack\n"
        f"Git revision: {revision}\n"
        f"Models: {len(models)}\n"
        "Install with: bash install-model-pack.sh /path/to/extracted/conda-pack\n",
        encoding="utf-8",
    )

    checksum_path = output_dir / "SHA256SUMS"
    files = sorted(
        path
        for path in output_dir.rglob("*")
        if path.is_file() and path != checksum_path and not path.name.endswith(".part")
    )
    with checksum_path.open("w", encoding="utf-8") as stream:
        for path in files:
            relative = path.relative_to(output_dir)
            stream.write(f"{digest(path, 'sha256')}  {relative.as_posix()}\n")


def create_archive(output_dir: Path, archive_path: Path) -> None:
    archive_path = archive_path.resolve()
    try:
        archive_path.relative_to(output_dir.resolve())
    except ValueError:
        pass
    else:
        raise ValueError("The archive must be outside the model-pack directory")

    archive_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "w:gz" if archive_path.name.endswith((".tar.gz", ".tgz")) else "w"
    print(f"Creating {archive_path} (this needs additional free disk space)...")
    if mode == "w:gz":
        archive_file = tarfile.open(archive_path, mode, compresslevel=1)
    else:
        archive_file = tarfile.open(archive_path, mode)
    with archive_file:
        archive_file.add(output_dir, arcname=output_dir.name)
    checksum = digest(archive_path, "sha256")
    archive_path.with_name(archive_path.name + ".sha256").write_text(
        f"{checksum}  {archive_path.name}\n", encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("dist/sammie-roto-2-models"),
        help="model-pack directory (default: dist/sammie-roto-2-models)",
    )
    parser.add_argument(
        "--archive",
        type=Path,
        help="optional .tar or .tar.gz to create after downloading",
    )
    internal = parser.add_mutually_exclusive_group()
    internal.add_argument(
        "--weights-only",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    internal.add_argument(
        "--remove-registered",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    output_dir = args.output if args.output.is_absolute() else repo_root / args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    models = read_registry(repo_root / "sammie" / "model_downloader.py")

    if args.remove_registered:
        for model in models:
            destination = output_dir / model["dest_dir"] / model["filename"]
            destination.unlink(missing_ok=True)
            destination.with_name(destination.name + ".part").unlink(missing_ok=True)
        print(f"Removed {len(models)} registry-managed model paths from {output_dir}")
        return 0

    if args.weights_only:
        if args.archive:
            raise ValueError("--archive cannot be combined with --weights-only")
        print(f"Downloading {len(models)} model files to {output_dir}")
        for model in models:
            download(model, output_dir)
        return 0

    copy_checkpoint_metadata(repo_root, output_dir)
    shutil.copy2(repo_root / "packaging" / "install-model-pack.sh", output_dir)

    print(f"Downloading {len(models)} model files to {output_dir}")
    for model in models:
        download(model, output_dir)

    write_manifests(repo_root, output_dir, models)
    if args.archive:
        archive_path = args.archive if args.archive.is_absolute() else repo_root / args.archive
        create_archive(output_dir, archive_path)

    print(f"Model pack ready: {output_dir}")
    print("Carry this directory beside the conda-pack archive.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nDownload interrupted; rerun the command to resume.", file=sys.stderr)
        raise SystemExit(130)
