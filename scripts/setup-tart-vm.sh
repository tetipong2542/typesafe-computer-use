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

cat <<EOF

==========================================================
 Setup complete! Next steps:
==========================================================
1. Start the VM with GUI to configure Accessibility permissions:
   tart run --dir=workspace:"$(pwd)" ${VM_NAME}

2. Inside the VM:
   - Open System Settings > Privacy & Security
   - Enable 'Accessibility' for Terminal
   - Enable 'Screen Recording' for Terminal
   - Enable 'Remote Login' and 'Screen Sharing' (for VNC Takeover)
   - Change default password (admin / admin)

3. Install Worker runtime on VM local disk (avoids VirtIO-FS startup mount race):
   rsync -av --exclude='.venv' --exclude='runs' "/Volumes/My Shared Files/workspace/" ~/typesafe-computer-use/
   cd ~/typesafe-computer-use
   uv sync

   # Configure guest-local secret (.env) with restricted permissions (chmod 600)
   echo "WORKER_AUTH_TOKEN=\$(openssl rand -hex 32)" > .env
   chmod 600 .env

   # Install and bootstrap per-user LaunchAgent (Aqua/WindowServer session)
   mkdir -p ~/Library/LaunchAgents
   cp scripts/com.typesafe.worker.plist ~/Library/LaunchAgents/com.typesafe.worker.plist
   plutil -lint ~/Library/LaunchAgents/com.typesafe.worker.plist
   launchctl bootout "gui/\$(id -u)" ~/Library/LaunchAgents/com.typesafe.worker.plist 2>/dev/null || true
   launchctl bootstrap "gui/\$(id -u)" ~/Library/LaunchAgents/com.typesafe.worker.plist
   launchctl enable "gui/\$(id -u)/com.typesafe.worker"
   launchctl kickstart -k "gui/\$(id -u)/com.typesafe.worker"

   # Enable TCC permissions for the actual executing process:
   # ~/typesafe-computer-use/.venv/bin/python
   # In System Settings > Privacy & Security > Accessibility and Screen Recording

4. Obtain VM IP from Host:
   VM_IP=\$(tart ip ${VM_NAME})
   echo "VM IP: \${VM_IP}"

5. You can now run E2E verification from Host:
   ./scripts/tart-preflight.sh
   ./scripts/e2e-tart-worker.sh
==========================================================
EOF
