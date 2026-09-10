#!/usr/bin/env bash
set -Eeuo pipefail

###############################################################################
# RTX 5050 Blackwell / llama.cpp CUDA installer
#
# Target:
#   Ubuntu 26.04
#   NVIDIA driver already installed
#   CUDA Toolkit 13.4
#   Python 3.14
#   Existing project-local .venv
#
# This script:
#   - does NOT install or modify the NVIDIA driver
#   - does NOT install pip NVIDIA CUDA runtime packages
#   - removes the old pip-CUDA ld.so configuration if present
#   - installs CUDA Toolkit 13.4 from NVIDIA
#   - builds llama-cpp-python from source with CUDA / Blackwell support
###############################################################################

# Always resolve paths relative to this script, not to the caller's pwd.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

CUDA_VERSION="13-4"
CUDA_HOME="/usr/local/cuda-13.4"
VENV_DIR="${SCRIPT_DIR}/.venv"
PYTHON="${VENV_DIR}/bin/python"
ENV_FILE="${VENV_DIR}/cuda-env.sh"

export CUDA_HOME

echo
echo "============================================================"
echo " llama.cpp / CUDA installation for RTX 5050 Blackwell"
echo "============================================================"
echo
echo "Project directory:"
echo "  ${SCRIPT_DIR}"
echo
echo "Python environment:"
echo "  ${VENV_DIR}"
echo

###############################################################################
# 1. Check OS
###############################################################################

echo "[1/9] Checking operating system..."

if [[ ! -r /etc/os-release ]]; then
    echo "ERROR: Cannot determine operating system."
    exit 1
fi

source /etc/os-release

echo "OS: ${PRETTY_NAME:-unknown}"

if [[ "${ID:-}" != "ubuntu" ]]; then
    echo "ERROR: This script expects Ubuntu."
    exit 1
fi

###############################################################################
# 2. Check NVIDIA driver / GPU
###############################################################################

echo
echo "[2/9] Checking NVIDIA driver / GPU..."

if ! command -v nvidia-smi >/dev/null 2>&1; then
    echo "ERROR: nvidia-smi was not found."
    echo "Install/fix the NVIDIA driver first."
    exit 1
fi

nvidia-smi

echo
echo "NVIDIA driver will NOT be changed by this script."

###############################################################################
# 3. Remove old pip CUDA linker configuration
###############################################################################

echo
echo "[3/9] Removing old pip-CUDA linker configuration..."

if [[ -f /etc/ld.so.conf.d/pip-nvidia-cuda.conf ]]; then
    echo "Removing:"
    echo "  /etc/ld.so.conf.d/pip-nvidia-cuda.conf"

    sudo rm -f /etc/ld.so.conf.d/pip-nvidia-cuda.conf
    sudo ldconfig
else
    echo "No old pip-CUDA linker configuration found."
fi

# Prevent an old shell environment from affecting the build.
unset LD_LIBRARY_PATH || true

###############################################################################
# 4. Install required system build dependencies
###############################################################################

echo
echo "[4/9] Installing build dependencies..."

sudo apt-get update

sudo apt-get install -y \
    autoconf \
    automake \
    libtool \
    pkg-config \
    build-essential \
    cmake \
    ninja-build \
    git \
    wget \
    curl

###############################################################################
# 5. Configure NVIDIA CUDA repository + install CUDA 13.4
###############################################################################

echo
echo "[5/9] Configuring NVIDIA CUDA repository..."

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

KEYRING_URL="https://developer.download.nvidia.com/compute/cuda/repos/ubuntu2604/x86_64/cuda-keyring_1.1-1_all.deb"

cd "${TMP_DIR}"

echo "Downloading CUDA repository keyring..."
wget -q --show-progress "${KEYRING_URL}" -O cuda-keyring.deb

echo "Installing CUDA repository keyring..."
sudo dpkg -i cuda-keyring.deb

sudo apt-get update

echo
echo "Checking for CUDA Toolkit ${CUDA_VERSION}..."

if ! apt-cache show "cuda-toolkit-${CUDA_VERSION}" >/dev/null 2>&1; then
    echo
    echo "ERROR: cuda-toolkit-${CUDA_VERSION} is not available."
    echo
    echo "Available CUDA toolkit packages:"
    apt-cache search '^cuda-toolkit-[0-9]' | head -30
    exit 1
fi

echo "Installing CUDA Toolkit ${CUDA_VERSION}..."
sudo apt-get install -y "cuda-toolkit-${CUDA_VERSION}"

# Return to the project directory.
cd "${SCRIPT_DIR}"

###############################################################################
# 6. Verify CUDA toolkit + existing Python venv
###############################################################################

echo
echo "[6/9] Verifying CUDA Toolkit..."

if [[ ! -x "${CUDA_HOME}/bin/nvcc" ]]; then
    echo "ERROR: CUDA compiler not found:"
    echo "  ${CUDA_HOME}/bin/nvcc"
    echo
    echo "Installed CUDA directories:"
    ls -ld /usr/local/cuda* 2>/dev/null || true
    exit 1
fi

export PATH="${CUDA_HOME}/bin:${PATH}"
export LD_LIBRARY_PATH="${CUDA_HOME}/lib64"

echo
echo "CUDA_HOME:"
echo "  ${CUDA_HOME}"

echo
echo "nvcc:"
"${CUDA_HOME}/bin/nvcc" --version

echo
echo "CUDA directories:"
ls -ld /usr/local/cuda* 2>/dev/null || true

echo
echo "[6/9] Verifying existing Python virtual environment..."

if [[ ! -x "${PYTHON}" ]]; then
    echo "ERROR: Python virtual environment not found:"
    echo "  ${VENV_DIR}"
    echo
    echo "Expected Python executable:"
    echo "  ${PYTHON}"
    echo
    echo "This script does NOT create or delete the project .venv."
    exit 1
fi

echo
echo "Python executable:"
echo "  ${PYTHON}"

"${PYTHON}" --version

PYTHON_MAJOR_MINOR="$("${PYTHON}" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"

if [[ "${PYTHON_MAJOR_MINOR}" != "3.14" ]]; then
    echo
    echo "ERROR: Expected Python 3.14, found Python ${PYTHON_MAJOR_MINOR}."
    exit 1
fi

###############################################################################
# 7. Install Python build tools and remove conflicting packages
###############################################################################

echo
echo "[7/9] Preparing Python environment..."

"${PYTHON}" -m pip install --upgrade \
    pip \
    setuptools \
    wheel

echo
echo "Removing potentially conflicting llama/CUDA Python packages..."

"${PYTHON}" -m pip uninstall -y \
    llama-cpp-python \
    nvidia-cuda-runtime-cu12 \
    nvidia-cublas-cu12 \
    nvidia-cuda-runtime-cu13 \
    nvidia-cublas-cu13 \
    >/dev/null 2>&1 || true

###############################################################################
# 8. Build llama-cpp-python from source
###############################################################################

echo
echo "[8/9] Building llama-cpp-python from source..."

export CUDA_HOME="${CUDA_HOME}"
export PATH="${CUDA_HOME}/bin:${PATH}"
export LD_LIBRARY_PATH="${CUDA_HOME}/lib64"

# CUDA backend enabled.
#
# Current llama.cpp handles compute 120 as Blackwell / 120a.
export CMAKE_ARGS="-DGGML_CUDA=ON -DGGML_NATIVE=ON -DCMAKE_CUDA_ARCHITECTURES=120"

# Initially disable CUDA graphs for a more conservative Blackwell baseline.
export GGML_CUDA_DISABLE_GRAPHS=1

echo
echo "CUDA_HOME:"
echo "  ${CUDA_HOME}"

echo
echo "CMAKE_ARGS:"
echo "  ${CMAKE_ARGS}"

echo
echo "CUDA graphs:"
echo "  disabled"

echo
echo "Installing llama-cpp-python..."
echo

"${PYTHON}" -m pip install \
    --no-cache-dir \
    --force-reinstall \
    --no-binary=:all: \
    llama-cpp-python

###############################################################################
# 9. Persist environment + validate installation
###############################################################################

echo
echo "[9/9] Writing CUDA environment file..."

cat > "${ENV_FILE}" <<EOF
# llama.cpp CUDA environment
export CUDA_HOME="${CUDA_HOME}"
export PATH="\${CUDA_HOME}/bin:\${PATH}"
export LD_LIBRARY_PATH="\${CUDA_HOME}/lib64"
export GGML_CUDA_DISABLE_GRAPHS=1
EOF

chmod 644 "${ENV_FILE}"

echo
echo "============================================================"
echo " Installation complete"
echo "============================================================"
echo

echo "Python:"
"${PYTHON}" --version

echo
echo "Python executable:"
echo "  ${PYTHON}"

echo
echo "llama-cpp-python:"
"${PYTHON}" -m pip show llama-cpp-python | grep -E '^(Name|Version):' || true

echo
echo "CUDA:"
"${CUDA_HOME}/bin/nvcc" --version

echo
echo "GPU:"
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader

echo
echo "Environment file:"
echo "  ${ENV_FILE}"

echo
echo "Testing GPU offload support..."

"${PYTHON}" - <<'PY'
from llama_cpp import llama_supports_gpu_offload

supported = llama_supports_gpu_offload()

print()
print("llama.cpp GPU offload support:", supported)

if not supported:
    raise SystemExit(
        "ERROR: llama-cpp-python installed, but GPU offload is unavailable."
    )

print("SUCCESS: llama-cpp-python reports GPU offload support.")
PY

