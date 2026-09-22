#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat <<'EOF'
Build a relocatable Sammie-Roto 2 conda-pack for Rocky Linux 9 (x86_64).

Usage:
  scripts/build-rocky9-conda-pack.sh [options]

Options:
  --backend PROFILE    cpu, cu126, cu130, rocm, or xpu (default: cpu)
  --include-models     Download and include every model checkpoint (~10 GB)
  --output PATH        Output .tar.gz path (default: dist/<generated name>)
  --help               Show this help

The script must run on Linux x86_64 and requires micromamba. It creates all
temporary environments outside the repository and removes them on exit.
EOF
}

backend="cpu"
include_models=0
output=""

while (( $# )); do
    case "$1" in
        --backend)
            [[ $# -ge 2 ]] || { echo "--backend requires a value" >&2; exit 2; }
            backend="$2"
            shift 2
            ;;
        --include-models)
            include_models=1
            shift
            ;;
        --output)
            [[ $# -ge 2 ]] || { echo "--output requires a value" >&2; exit 2; }
            output="$2"
            shift 2
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

case "$backend" in
    cpu|cu126|cu130|rocm|xpu) ;;
    *) echo "Unsupported backend: $backend" >&2; exit 2 ;;
esac

[[ "$(uname -s)" == "Linux" ]] || {
    echo "This pack must be built on Linux; use the GitHub workflow from macOS/Windows." >&2
    exit 1
}
[[ "$(uname -m)" == "x86_64" ]] || {
    echo "Only Rocky Linux 9 x86_64 targets are currently supported." >&2
    exit 1
}

for command in git ldd micromamba sha256sum tar; do
    command -v "$command" >/dev/null || {
        echo "Required command not found: $command" >&2
        exit 1
    }
done

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"
[[ -f pyproject.toml && -f uv.lock ]] || {
    echo "Run this script from the Sammie-Roto 2 repository." >&2
    exit 1
}
git_revision="$(git -C "$repo_root" rev-parse --verify HEAD)" || {
    echo "The repository must have a valid HEAD commit." >&2
    exit 1
}

if [[ -z "$output" ]]; then
    model_suffix=""
    (( include_models )) && model_suffix="-with-models"
    output="$repo_root/dist/sammie-roto-2-rocky9-x86_64-${backend}${model_suffix}.tar.gz"
elif [[ "$output" != /* ]]; then
    output="$repo_root/$output"
fi
[[ "$output" == *.tar.gz ]] || {
    echo "Output path must end in .tar.gz" >&2
    exit 2
}
mkdir -p "$(dirname -- "$output")"

build_root="$(mktemp -d "${TMPDIR:-/tmp}/sammie-condapack.XXXXXX")"
cleanup() {
    micromamba env remove --yes --prefix "$build_root/target" >/dev/null 2>&1 || true
    micromamba env remove --yes --prefix "$build_root/tools" >/dev/null 2>&1 || true
    rm -rf -- "$build_root"
}
trap cleanup EXIT

target_prefix="$build_root/target"
tools_prefix="$build_root/tools"
app_dir="$target_prefix/share/sammie-roto-2"
source_snapshot="$build_root/source"

echo "Creating a tracked source snapshot from $git_revision..."
mkdir -p "$source_snapshot"
git -C "$repo_root" archive --format=tar "$git_revision" | \
    tar -xf - -C "$source_snapshot"
required_snapshot_files=(
    pyproject.toml
    uv.lock
    packaging/conda-linux-64.yml
    packaging/sammie-roto
    packaging/sammie-roto-check
    packaging/sammie-roto-setup
    packaging/smoke_test.py
    scripts/build-model-pack.py
)
for relative_path in "${required_snapshot_files[@]}"; do
    [[ -f "$source_snapshot/$relative_path" ]] || {
        echo "HEAD is missing $relative_path; commit the packaging files before building." >&2
        exit 1
    }
done

echo "Creating the target conda environment..."
micromamba create --yes --prefix "$target_prefix" \
    --file "$source_snapshot/packaging/conda-linux-64.yml"

echo "Creating isolated packaging tools..."
micromamba create --yes --prefix "$tools_prefix" --channel conda-forge \
    python=3.12 uv=0.12.5 conda-pack

python_lock="$build_root/pylock.${backend}.toml"
echo "Exporting the locked Python dependency set for backend: $backend"
UV_CACHE_DIR="$build_root/uv-cache" \
    "$tools_prefix/bin/uv" export \
        --project "$source_snapshot" \
        --format pylock.toml \
        --frozen \
        --extra "$backend" \
        --no-dev \
        --no-emit-project \
        --output-file "$python_lock" \
        --no-python-downloads

echo "Installing the lock into the target conda environment..."
UV_CACHE_DIR="$build_root/uv-cache" \
    "$tools_prefix/bin/uv" pip install \
        --python "$target_prefix/bin/python" \
        --requirements "$python_lock" \
        --no-build \
        --strict \
        --no-python-downloads

echo "Copying the application into the environment..."
mkdir -p "$(dirname -- "$app_dir")"
mv "$source_snapshot" "$app_dir"

# Keep dependency-only packs small and ensure a with-models build can never
# trust a registry-managed weight merely because it existed in the source.
"$target_prefix/bin/python" "$app_dir/scripts/build-model-pack.py" \
    --output "$app_dir" \
    --remove-registered

install -m 0755 "$app_dir/packaging/sammie-roto" "$target_prefix/bin/sammie-roto"
install -m 0755 "$app_dir/packaging/sammie-roto-check" "$target_prefix/bin/sammie-roto-check"
install -m 0755 "$app_dir/packaging/sammie-roto-setup" "$target_prefix/bin/sammie-roto-setup"
install -m 0644 "$python_lock" "$app_dir/BUNDLE-PYTHON-LOCK.toml"

if (( include_models )); then
    echo "Downloading and verifying all model checkpoints..."
    "$target_prefix/bin/python" "$app_dir/scripts/build-model-pack.py" \
        --output "$app_dir" \
        --weights-only
fi

cat > "$app_dir/BUNDLE-INFO.txt" <<EOF
Sammie-Roto 2 offline conda-pack
Git revision: $git_revision
Target: Rocky Linux 9.x x86_64
PyTorch backend: $backend
Model checkpoints included: $([[ $include_models -eq 1 ]] && echo yes || echo no)
Built (UTC): $(date -u +'%Y-%m-%dT%H:%M:%SZ')
EOF
micromamba list --prefix "$target_prefix" --explicit > "$app_dir/BUNDLE-CONDA-SPECS.txt"
"$tools_prefix/bin/uv" pip freeze \
    --python "$target_prefix/bin/python" > "$app_dir/BUNDLE-PYTHON-PACKAGES.txt"
(
    cd "$app_dir"
    find checkpoints -type f -print0 | sort -z | xargs -0 sha256sum
) > "$app_dir/BUNDLE-MODEL-SHA256SUMS.txt"

echo "Running import, Qt, and bytecode checks..."
(
    cd "$app_dir"
    export PYTHONNOUSERSITE=1
    export PYTHONDONTWRITEBYTECODE=1
    export LD_LIBRARY_PATH="$target_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
    export QT_QPA_PLATFORM=offscreen
    PYTHONPYCACHEPREFIX="$build_root/pycache" \
        "$target_prefix/bin/python" -m compileall -q .
    "$target_prefix/bin/python" packaging/smoke_test.py
)

qxcb_plugin="$(find "$target_prefix" -type f \
    -path '*/PySide6/Qt/plugins/platforms/libqxcb.so' -print -quit)"
[[ -n "$qxcb_plugin" ]] || {
    echo "PySide6 xcb platform plugin was not found." >&2
    exit 1
}
missing_qxcb_libraries="$(
    LD_LIBRARY_PATH="$target_prefix/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
        ldd "$qxcb_plugin" | awk '/not found/ { print }'
)"
if [[ -n "$missing_qxcb_libraries" ]]; then
    echo "The Qt xcb plugin has unresolved native libraries:" >&2
    echo "$missing_qxcb_libraries" >&2
    exit 1
fi

echo "Creating relocatable archive..."
rm -f -- "$output" "$output.sha256"
"$tools_prefix/bin/conda-pack" \
    --prefix "$target_prefix" \
    --output "$output" \
    --format tar.gz \
    --compress-level 1

echo "Extracting at a new prefix and testing conda-unpack..."
rm -rf -- "$target_prefix"
relocated_prefix="$build_root/relocated"
mkdir -p "$relocated_prefix"
tar -xzf "$output" -C "$relocated_prefix"
"$relocated_prefix/bin/sammie-roto-setup"

(
    cd "$(dirname -- "$output")"
    sha256sum "$(basename -- "$output")" > "$(basename -- "$output").sha256"
)

echo "Created: $output"
echo "Checksum: $output.sha256"
