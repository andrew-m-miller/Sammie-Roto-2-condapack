#!/usr/bin/env bash
set -euo pipefail

usage() {
    echo "Usage: $0 /path/to/extracted/conda-pack" >&2
}

[[ $# -eq 1 ]] || { usage; exit 2; }

model_pack_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
bundle_prefix="$(cd -- "$1" 2>/dev/null && pwd)" || {
    echo "Bundle directory does not exist: $1" >&2
    exit 1
}
app_dir="$bundle_prefix/share/sammie-roto-2"

[[ -x "$bundle_prefix/bin/sammie-roto" && -d "$app_dir/checkpoints" ]] || {
    echo "Not a Sammie-Roto 2 conda-pack: $bundle_prefix" >&2
    exit 1
}

echo "Verifying the model pack..."
(
    cd "$model_pack_dir"
    sha256sum --check SHA256SUMS
)

echo "Installing models into $app_dir/checkpoints ..."
cp -a "$model_pack_dir/checkpoints/." "$app_dir/checkpoints/"
echo "Model installation complete."
