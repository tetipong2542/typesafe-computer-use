#!/usr/bin/env bash
# ==============================================================================
# setup-tart-vm.sh: Setup an isolated macOS VM using Tart for typesafe-computer-use
# ==============================================================================

set -euo pipefail

VM_NAME="${1:-macos-worker}"
BASE_IMAGE="${2:-ghcr.io/cirruslabs/macos-sonoma-base:latest}"
CPU="${3:-4}"
RAM="${4:-8192}"
DISPLAY="${5:-1440x900}"

echo "=========================================================="
echo " Setting up Tart macOS VM: ${VM_NAME}"
echo " Base Image: ${BASE_IMAGE}"
echo " Specs: ${CPU} CPUs, ${RAM} MB RAM, Display: ${DISPLAY}"
echo "=========================================================="

# 1. Verify Host Architecture
ARCH="$(uname -m)"
if [[ "${ARCH}" != "arm64" ]]; then
    echo "ERROR: Tart requires Apple Silicon (arm64). Current arch: ${ARCH}" >&2
    exit 1
fi

# 2. Check if Tart is installed
if ! command -v tart &>/dev/null; then
    echo "Tart is not found in PATH. Installing via Homebrew..."
    brew install cirruslabs/cli/tart
fi

echo "Tart version: $(tart --version)"

# 3. Pull / Clone VM Base Image
if tart list | grep -q "^${VM_NAME}\$"; then
    echo "VM '${VM_NAME}' already exists. Skipping clone."
else
    echo "Cloning ${BASE_IMAGE} into '${VM_NAME}' (this may take a few minutes)..."
    tart clone "${BASE_IMAGE}" "${VM_NAME}"
fi

# 4. Configure VM Resources
echo "Configuring VM resources..."
tart set "${VM_NAME}" --cpu "${CPU}" --memory "${RAM}" --display "${DISPLAY}"

echo ""
echo "=========================================================="
echo " Setup complete! Next steps:"
echo "=========================================================="
echo "1. Start the VM with GUI to configure Accessibility permissions:"
echo "   tart run --dir=workspace:\"$(pwd)\" ${VM_NAME}"
echo ""
echo "2. Inside the VM:"
echo "   - Open System Settings > Privacy & Security"
echo "   - Enable 'Accessibility' for Terminal"
echo "   - Enable 'Screen Recording' for Terminal"
echo "   - Enable 'Remote Login' and 'Screen Sharing' (for VNC Takeover)"
echo "   - Change default password (admin / admin)"
echo ""
echo "3. Run the Worker API inside the VM:"
echo "   cd /Volumes/My\\ Shared\\ Files/workspace"
echo "   uv run clicker-worker"
echo ""
echo "4. Obtain VM IP from Host:"
echo "   VM_IP=\$(tart ip ${VM_NAME})"
echo "   echo \"VM IP: \${VM_IP}\""
echo ""
echo "5. You can now send goals to: http://\${VM_IP}:8000/tasks"
echo "=========================================================="
