# Air-gapped Rocky Linux 9.5 bundle

The Rocky Linux workflow builds one relocatable `conda-pack` archive containing
Python 3.12, native GUI libraries, the dependencies locked by `uv.lock`, and the
Sammie-Roto 2 source tree. No Conda installation is needed on the destination.

The archive targets **Rocky Linux 9.x on x86_64**. It is not an ARM build.

## Choose a backend

Build exactly one backend for the destination computer:

| Profile | Hardware |
| --- | --- |
| `cpu` | No supported GPU; slow but the most portable |
| `cu126` | NVIDIA GPUs requiring the CUDA 12.6 PyTorch build |
| `cu130` | Newer NVIDIA GPUs using the CUDA 13.0 PyTorch build |
| `rocm` | Supported AMD GPUs |
| `xpu` | Supported Intel Arc/Xe GPUs |

The CUDA/ROCm/XPU user-space libraries are packed. Kernel GPU drivers cannot be
made portable and must already be installed on the Rocky computer. The driver
must be compatible with the selected PyTorch backend.

## Build with GitHub Actions

1. Open **Actions** in the GitHub repository.
2. Select **Build Rocky Linux 9 offline conda-pack**.
3. Choose **Run workflow**, select the backend, and decide whether to include
   model weights.
4. Download the artifact from the completed workflow run and unzip the artifact
   wrapper. It contains the `.tar.gz` bundle and its `.sha256` file.

Model weights add roughly 10 GB before archive compression. Disabling that
option makes a dependency-only bundle, but model features will try to download
their missing weights and therefore will not work on an air-gapped machine.

Standard GitHub-hosted Linux runners have a 14 GB SSD, which is generally not
enough to build a model-inclusive bundle and may also be too small for some GPU
profiles. For a complete bundle, run the builder on a connected Rocky Linux 9
machine with ample free space or change the workflow to use a larger/self-hosted
Linux x86_64 runner. See GitHub's
[runner specifications](https://docs.github.com/en/actions/reference/runners/github-hosted-runners).

## Download a separate model pack on macOS, Windows, or Linux

The model files are platform-independent, so they can be downloaded on this
machine and carried beside a smaller dependency-only conda-pack. The downloader
uses only Python's standard library, reads the model registry directly from the
application source, resumes `.part` files, and verifies the registry's checksum
for every completed download:

```bash
python3 scripts/build-model-pack.py
```

The result is `dist/sammie-roto-2-models/`. Copy that whole directory and the
conda-pack archive to the external drive. Keeping it as a directory avoids
needing another ~10 GB of temporary space for an archive containing already
compressed model data. Use exFAT, NTFS, APFS, ext4, or another filesystem that
supports files larger than 4 GB; FAT32 may be too small for individual weights.
If a single file is required and enough disk is free:

```bash
python3 scripts/build-model-pack.py \
  --archive dist/sammie-roto-2-models.tar
```

On the offline Rocky computer, extract and set up the conda-pack first, then
install the companion model pack:

```bash
bash /media/transfer/sammie-roto-2-models/install-model-pack.sh \
  "$HOME/opt/sammie-roto-2"
```

The installer verifies `SHA256SUMS` before copying anything. The model pack is
independent of the selected CPU/CUDA/ROCm/XPU profile and can be reused with
future bundles unless the application's model registry changes.

## Build on another connected Linux x86_64 computer

Install `micromamba`, then run from the repository root:

```bash
bash scripts/build-rocky9-conda-pack.sh --backend cu126 --include-models
```

The builder packages only files tracked by the current `HEAD` commit. Commit the
packaging changes you intend to ship first; ignored and untracked working-tree
files are never copied into the archive.

For the closest compatibility guarantee, build in Rocky Linux 9.5; the GitHub
workflow does this automatically. Allow roughly 35 GB of free space for a CPU
bundle with all models and more for a GPU bundle. The output is written to
`dist/`.

## Transfer and install offline

Copy both downloaded files to the Rocky machine and verify the archive before
extracting it:

```bash
archive=sammie-roto-2-rocky9-x86_64-cpu.tar.gz
sha256sum -c "$archive.sha256"
mkdir -p "$HOME/opt/sammie-roto-2"
tar -xzf "$archive" -C "$HOME/opt/sammie-roto-2"
"$HOME/opt/sammie-roto-2/bin/sammie-roto-setup"
```

Substitute the archive name for the selected backend and add `-with-models` when
the weights were embedded. The setup command fixes embedded paths and runs a
headless dependency check. Run it only after placing the extracted directory in
its final location. To move the installation later, extract a fresh copy of the
archive at the new location.

Launch the application from a graphical desktop session:

```bash
"$HOME/opt/sammie-roto-2/bin/sammie-roto"
```

The launcher automatically performs first-run relocation if the explicit setup
step was skipped. It ignores user-site Python packages so packages installed in
the user's home directory cannot shadow the tested environment.

## Validate or audit the bundle

Run the non-interactive check at any time:

```bash
"$HOME/opt/sammie-roto-2/bin/sammie-roto-check"
```

The installed application directory also contains:

- `BUNDLE-INFO.txt`: source revision, backend, target, and model inclusion
- `BUNDLE-CONDA-SPECS.txt`: exact Conda package URLs
- `BUNDLE-PYTHON-LOCK.toml`: hashed PEP 751 lock used for installation
- `BUNDLE-PYTHON-PACKAGES.txt`: installed Python packages
- `BUNDLE-MODEL-SHA256SUMS.txt`: model/configuration file checksums

The bundle deliberately does not run the repository's online installer or
updater. Updates require building and transferring a new verified archive.
