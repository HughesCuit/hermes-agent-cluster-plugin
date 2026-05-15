#!/usr/bin/env bash
#
# install.sh — One-command install for hermes-agent-cluster
#
# Downloads/compiles the hermes-cluster Go binary and installs
# the Hermes Agent plugin.
#
# Usage:
#   bash install.sh                    # default: build from source (needs Go)
#   bash install.sh --download         # download pre-built binary from GitHub Releases
#   bash install.sh --from <path>      # copy binary from existing source tree
#   bash install.sh --help             # show usage

set -euo pipefail

PLUGIN_NAME="hermes-agent-cluster"
BINARY_NAME="hermes-cluster"
REPO_URL="https://github.com/HughesCuit/hermes-agent-cluster.git"
PLUGIN_DIR="${HOME}/.hermes/plugins/${PLUGIN_NAME}"
BIN_DIR="${HOME}/.local/bin"
HERMES_CLUSTER_BIN="${BIN_DIR}/${BINARY_NAME}"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

info()  { echo -e "${CYAN}[INFO]${NC} $1"; }
ok()    { echo -e "${GREEN}[OK]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()   { echo -e "${RED}[ERR]${NC} $1"; }

# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

show_help() {
    cat <<'HELP'
Usage: bash install.sh [OPTION]

Install hermes-agent-cluster plugin for Hermes Agent.

Options:
  --download         Download pre-built binary from GitHub Releases
                     (falls back to source build if no assets found)
  --from <path>      Copy binary from an existing hermes-agent-cluster source tree
  --help             Show this help message

Without options, builds hermes-cluster from source (requires Go 1.22+).

Environment:
  HERMES_CLUSTER_VERSION   Version to download (default: latest)
HELP
    exit 0
}

# ---------------------------------------------------------------------------
# Parse args
# ---------------------------------------------------------------------------

DOWNLOAD=false
FROM_SOURCE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --help) show_help ;;
        --download) DOWNLOAD=true; shift ;;
        --from)
            FROM_SOURCE="$2"
            shift 2
            ;;
        *) err "Unknown option: $1"; show_help ;;
    esac
done

# ---------------------------------------------------------------------------
# Step 1: Install the Hermes Agent plugin
# ---------------------------------------------------------------------------

info "Installing ${PLUGIN_NAME} plugin..."

# The plugin files are in the same directory as this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mkdir -p "${PLUGIN_DIR}"
cp -r "${SCRIPT_DIR}/plugin.yaml" "${SCRIPT_DIR}/__init__.py" "${PLUGIN_DIR}/" 2>/dev/null || {
    err "Failed to copy plugin files. Make sure you're running install.sh from the repo root."
    exit 1
}
ok "Plugin installed to ${PLUGIN_DIR}"

# ---------------------------------------------------------------------------
# Step 2: Install the hermes-cluster binary
# ---------------------------------------------------------------------------

install_binary_from_source() {
    info "Building hermes-cluster from source..."
    local tmp_dir
    tmp_dir="$(mktemp -d)"

    if command -v go &>/dev/null; then
        git clone --depth 1 "${REPO_URL}" "${tmp_dir}/src" 2>/dev/null || {
            warn "Git clone failed. Check internet connection."
            rm -rf "${tmp_dir}"
            return 1
        }

        (cd "${tmp_dir}/src" && go build -o "${HERMES_CLUSTER_BIN}" ./cmd/cluster) || {
            warn "Go build failed. Check Go environment (needs Go 1.22+)."
            rm -rf "${tmp_dir}"
            return 1
        }

        chmod +x "${HERMES_CLUSTER_BIN}"
        rm -rf "${tmp_dir}"
        ok "Built hermes-cluster -> ${HERMES_CLUSTER_BIN}"
        return 0
    else
        rm -rf "${tmp_dir}"
        err "Go is not installed. Install Go 1.22+ or use --download."
        return 1
    fi
}

install_binary_from_download() {
    info "Attempting to download hermes-cluster binary..."

    if ! command -v curl &>/dev/null; then
        warn "curl not found, cannot download binary."
        return 1
    fi

    local version="${HERMES_CLUSTER_VERSION:-latest}"
    local arch="$(uname -m)"
    local os="$(uname -s | tr '[:upper:]' '[:lower:]')"

    # Map arch names
    case "${arch}" in
        x86_64) arch="amd64" ;;
        aarch64|arm64) arch="arm64" ;;
    esac

    local asset_name="${BINARY_NAME}-${os}-${arch}"
    if [[ "${version}" == "latest" ]]; then
        local download_url
        download_url=$(curl -sf "https://api.github.com/repos/HughesCuit/hermes-agent-cluster/releases/latest" \
            | python3 -c "import json,sys; r=json.load(sys.stdin); [print(a['browser_download_url']) for a in r.get('assets',[]) if '${asset_name}' in a['name']]" 2>/dev/null || true)

        if [[ -z "${download_url}" ]]; then
            warn "No pre-built binary found for ${asset_name} in latest release."
            return 1
        fi
    else
        download_url="https://github.com/HughesCuit/hermes-agent-cluster/releases/download/${version}/${asset_name}"
    fi

    info "Downloading ${download_url}..."
    mkdir -p "${BIN_DIR}"
    curl -sfL "${download_url}" -o "${HERMES_CLUSTER_BIN}" || {
        warn "Download failed."
        return 1
    }

    chmod +x "${HERMES_CLUSTER_BIN}"
    ok "Downloaded hermes-cluster -> ${HERMES_CLUSTER_BIN}"
    return 0
}

install_binary_from_path() {
    local src="$1"
    if [[ ! -f "${src}/cmd/cluster/main.go" ]]; then
        err "Source directory doesn't look like hermes-agent-cluster: ${src}"
        return 1
    fi
    info "Building hermes-cluster from ${src}..."
    mkdir -p "${BIN_DIR}"
    (cd "${src}" && go build -o "${HERMES_CLUSTER_BIN}" ./cmd/cluster) || {
        err "Go build failed."
        return 1
    }
    chmod +x "${HERMES_CLUSTER_BIN}"
    ok "Built hermes-cluster -> ${HERMES_CLUSTER_BIN}"
}

# ── Decide how to get the binary ──────────────────────────────────────

if [[ -n "${FROM_SOURCE}" ]]; then
    install_binary_from_path "${FROM_SOURCE}"
elif [[ "${DOWNLOAD}" == true ]]; then
    install_binary_from_download || install_binary_from_source || {
        err "Could not install hermes-cluster binary."
        warn "Install Go and re-run this script, or manually build:"
        echo "  git clone https://github.com/HughesCuit/hermes-agent-cluster.git"
        echo "  cd hermes-agent-cluster && go build -o ~/.local/bin/hermes-cluster ./cmd/cluster"
        exit 1
    }
else
    # Default: try download first (no Go needed), fall back to source build
    install_binary_from_download || install_binary_from_source || {
        err "Could not install hermes-cluster binary."
        warn "Install Go 1.22+ and re-run, or use: bash install.sh --download"
        exit 1
    }
fi

# ---------------------------------------------------------------------------
# Step 3: Create example config
# ---------------------------------------------------------------------------

CONFIG_DIR="${HOME}/.hermes/agent-cluster"
mkdir -p "${CONFIG_DIR}"

if [[ ! -f "${CONFIG_DIR}/cluster.yaml" ]]; then
    cat > "${CONFIG_DIR}/cluster.yaml" <<'CFG'
# hermes-agent-cluster configuration
# Copy this file and edit for your setup.
#
# For a main node:
cluster:
  id: my-cluster
  role: main
  token: ""

node:
  id: node_main
  name: main-node
  capabilities:
    - planning
    - reviewing
    - scheduling

server:
  bind: "0.0.0.0"
  port: 8787

lease:
  ttl: 30s
  scan_rate: 5s

watchdog:
  check_interval: 3s
  degraded_after: 10s
  offline_after: 20s
CFG
    ok "Created example config → ${CONFIG_DIR}/cluster.yaml"
    info "Edit ${CONFIG_DIR}/cluster.yaml before starting the cluster."
fi

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------

info "Verifying installation..."

# Check plugin
if [[ -f "${PLUGIN_DIR}/plugin.yaml" && -f "${PLUGIN_DIR}/__init__.py" ]]; then
    ok "Plugin files present at ${PLUGIN_DIR}"
else
    warn "Plugin files missing at ${PLUGIN_DIR}"
fi

# Check binary
if command -v hermes-cluster &>/dev/null || [[ -x "${HERMES_CLUSTER_BIN}" ]]; then
    ok "hermes-cluster binary ready"
    hermes-cluster --help 2>/dev/null || echo "  (run 'hermes-cluster -config <path>' to start)"
else
    warn "hermes-cluster binary not found in PATH or ${BIN_DIR}"
fi

# Final message
echo ""
echo -e "${GREEN}┌─────────────────────────────────────────────────────┐${NC}"
echo -e "${GREEN}│${NC}  hermes-agent-cluster installed!                    ${GREEN}│${NC}"
echo -e "${GREEN}│${NC}                                                     ${GREEN}│${NC}"
echo -e "${GREEN}│${NC}  Plugin:    ${CYAN}${PLUGIN_DIR}${NC}       ${GREEN}│${NC}"
echo -e "${GREEN}│${NC}  Binary:    ${CYAN}${HERMES_CLUSTER_BIN}${NC}   ${GREEN}│${NC}"
echo -e "${GREEN}│${NC}  Config:    ${CYAN}${CONFIG_DIR}/cluster.yaml${NC}  ${GREEN}│${NC}"
echo -e "${GREEN}│${NC}                                                     ${GREEN}│${NC}"
echo -e "${GREEN}│${NC}  Next steps:                                       ${GREEN}│${NC}"
echo -e "${GREEN}│${NC}  1. Restart Hermes Agent to load the plugin        ${GREEN}│${NC}"
echo -e "${GREEN}│${NC}  2. Use ${YELLOW}kanban_cluster_init${NC} to start a cluster      ${GREEN}│${NC}"
echo -e "${GREEN}│${NC}  3. On other nodes: ${YELLOW}kanban_cluster_join${NC}              ${GREEN}│${NC}"
echo -e "${GREEN}│${NC}                                                     ${GREEN}│${NC}"
echo -e "${GREEN}│${NC}  Or install via Hermes CLI:                         ${GREEN}│${NC}"
echo -e "${GREEN}│${NC}    ${YELLOW}hermes plugins install HughesCuit/hermes-agent-cluster-plugin${NC}  ${GREEN}│${NC}"
echo -e "${GREEN}└─────────────────────────────────────────────────────┘${NC}"
