"""
hermes-agent-cluster plugin — Distributed Hermes cluster coordination.

Auto-installs the hermes-cluster Go binary (builds from source or downloads),
then registers 7 kanban_cluster_* tools for multi-node task orchestration.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.error import URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLUGIN_NAME = "hermes-agent-cluster"
CLUSTER_BINARY = "hermes-cluster"
RELEASES_URL = "https://api.github.com/repos/HughesCuit/hermes-agent-cluster/releases/latest"
REPO_URL = "https://github.com/HughesCuit/hermes-agent-cluster.git"

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_cluster_config: Dict[str, Any] = {}
_heartbeat_thread: Optional[threading.Thread] = None
_heartbeat_stop = threading.Event()
_binary_ready = False


# ---------------------------------------------------------------------------
# Binary management
# ---------------------------------------------------------------------------

def _get_plugin_dir() -> Path:
    """Return the plugin directory under ~/.hermes/plugins/."""
    return Path(__file__).resolve().parent


def _download_binary() -> Optional[Path]:
    """Download pre-built hermes-cluster binary from GitHub Releases."""
    import platform
    import tempfile

    target = _get_plugin_dir() / CLUSTER_BINARY
    if target.exists():
        return target

    # Detect OS and architecture
    system = platform.system().lower()  # Linux, Darwin, Windows
    machine = platform.machine().lower()  # x86_64, aarch64

    # Map to release asset naming
    arch_map = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
    arch = arch_map.get(machine, machine)
    os_name = system

    asset_name = f"{CLUSTER_BINARY}-{os_name}-{arch}"
    logger.info("Downloading %s from GitHub Releases...", asset_name)

    try:
        # Get latest release info
        import urllib.request
        import json

        api_url = "https://api.github.com/repos/HughesCuit/hermes-agent-cluster/releases/latest"
        req = urllib.request.Request(api_url, headers={"User-Agent": "hermes-agent-cluster-plugin"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            release = json.loads(resp.read().decode())

        # Find matching asset
        download_url = None
        for asset in release.get("assets", []):
            if asset_name in asset["name"]:
                download_url = asset["browser_download_url"]
                break

        if not download_url:
            logger.warning("No pre-built binary found for %s in release %s",
                          asset_name, release.get("tag_name", "latest"))
            return None

        # Download
        logger.info("Downloading from %s...", download_url)
        with tempfile.NamedTemporaryFile(delete=False) as tmp:
            req = urllib.request.Request(download_url, headers={"User-Agent": "hermes-agent-cluster-plugin"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                tmp.write(resp.read())
            tmp_path = tmp.name

        # Move to target
        import shutil as _shutil
        _shutil.move(tmp_path, str(target))
        os.chmod(target, 0o755)
        logger.info("Downloaded hermes-cluster to %s", target)
        return target

    except Exception as e:
        logger.warning("Download failed: %s", e)
        return None


def _find_or_install_binary() -> Optional[Path]:
    """Find hermes-cluster in PATH or plugin dir. Auto-download/build if missing."""
    # 1. Check PATH
    which = shutil.which(CLUSTER_BINARY)
    if which:
        logger.info("Found hermes-cluster in PATH: %s", which)
        return Path(which)

    # 2. Check plugin dir
    local_bin = _get_plugin_dir() / CLUSTER_BINARY
    if local_bin.exists():
        logger.info("Found hermes-cluster in plugin dir: %s", local_bin)
        os.chmod(local_bin, 0o755)
        return local_bin

    # 3. Check ~/.local/bin
    local_bin2 = Path.home() / ".local" / "bin" / CLUSTER_BINARY
    if local_bin2.exists():
        logger.info("Found hermes-cluster in ~/.local/bin: %s", local_bin2)
        return local_bin2

    # 4. Try to download pre-built binary from GitHub Releases
    downloaded = _download_binary()
    if downloaded:
        return downloaded

    # 5. Fallback: build from source (needs Go)
    built = _build_from_source()
    if built:
        return built

    logger.warning(
        "hermes-cluster binary not found. "
        "Run the install script: bash %s/install.sh --download",
        _get_plugin_dir(),
    )
    return None


def _build_from_source() -> Optional[Path]:
    """Clone main repo and build hermes-cluster binary."""
    go_exe = shutil.which("go")
    if not go_exe:
        logger.warning("Go is not installed. Cannot build hermes-cluster from source.")
        return None

    import tempfile

    target = _get_plugin_dir() / CLUSTER_BINARY
    if target.exists():
        return target

    logger.info("Building hermes-cluster from source (this may take a moment)...")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", REPO_URL, str(tmp_path / "src")],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                logger.warning("Git clone failed: %s", result.stderr.strip())
                return None

            src_dir = tmp_path / "src"
            result = subprocess.run(
                [go_exe, "build", "-o", str(target), "./cmd/cluster"],
                capture_output=True, text=True, timeout=300,
                cwd=str(src_dir),
            )
            if result.returncode != 0:
                logger.warning("Go build failed: %s", result.stderr.strip())
                return None

            os.chmod(target, 0o755)
            logger.info("Built hermes-cluster at %s", target)
            return target

        except subprocess.TimeoutExpired:
            logger.warning("Build timed out.")
            return None
        except FileNotFoundError as e:
            logger.warning("Build failed: %s", e)
            return None


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _api_call(base_url: str, method: str, path: str, data: dict = None) -> dict:
    url = f"{base_url}{path}"
    body = json.dumps(data).encode() if data else None
    req = Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    try:
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except URLError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_cluster_init(args: dict, **kwargs) -> str:
    """Initialize a new cluster (main node)."""
    port = args.get("port", 8787)
    node_id = args.get("node_id", "node_main")
    capabilities = args.get("capabilities", ["planning", "reviewing", "scheduling"])

    config_dir = Path.home() / ".hermes" / "agent-cluster"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "cluster.yaml"

    config_content = f"""cluster:
  id: {args.get("cluster_id", "hermes-cluster")}
  role: main
  token: "{args.get("token", "")}"

node:
  id: {node_id}
  name: main-node
  capabilities:
{chr(10).join(f"    - {c}" for c in capabilities)}

server:
  bind: "0.0.0.0"
  port: {port}

lease:
  ttl: 30s
  scan_rate: 5s

watchdog:
  check_interval: 3s
  degraded_after: 10s
  offline_after: 20s
"""
    config_path.write_text(config_content)

    binary = _find_or_install_binary()
    if not binary:
        return json.dumps({"error": "hermes-cluster binary not found. Run the install script first."})

    try:
        proc = subprocess.Popen(
            [str(binary), "-config", str(config_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _cluster_config["process"] = proc
        _cluster_config["base_url"] = f"http://127.0.0.1:{port}"
        _cluster_config["node_id"] = node_id
        _cluster_config["role"] = "main"
        time.sleep(1)

        return json.dumps({
            "status": "initialized",
            "node_id": node_id,
            "role": "main",
            "port": port,
            "pid": proc.pid,
            "config": str(config_path),
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def handle_cluster_join(args: dict, **kwargs) -> str:
    """Join an existing cluster as worker."""
    endpoint = args.get("endpoint", "http://127.0.0.1:8787")
    node_id = args.get("node_id", "node_worker")
    capabilities = args.get("capabilities", ["coding", "gpu", "browser"])
    port = args.get("port", 8788)

    config_dir = Path.home() / ".hermes" / "agent-cluster"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "cluster-worker.yaml"

    config_content = f"""cluster:
  id: {args.get("cluster_id", "hermes-cluster")}
  role: worker
  endpoint: "{endpoint}"
  token: "{args.get("token", "")}"

node:
  id: {node_id}
  name: worker-node
  capabilities:
{chr(10).join(f"    - {c}" for c in capabilities)}

server:
  bind: "0.0.0.0"
  port: {port}

lease:
  ttl: 30s
  scan_rate: 5s

watchdog:
  check_interval: 3s
  degraded_after: 10s
  offline_after: 20s
"""
    config_path.write_text(config_content)

    binary = _find_or_install_binary()
    if not binary:
        return json.dumps({"error": "hermes-cluster binary not found. Run the install script first."})

    try:
        proc = subprocess.Popen(
            [str(binary), "-config", str(config_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        _cluster_config["process"] = proc
        _cluster_config["base_url"] = f"http://127.0.0.1:{port}"
        _cluster_config["node_id"] = node_id
        _cluster_config["role"] = "worker"
        time.sleep(1)

        result = _api_call(endpoint, "POST", "/api/v1/nodes/join", {
            "node_name": node_id,
            "capabilities": capabilities,
            "endpoint": f"http://127.0.0.1:{port}",
        })

        return json.dumps({
            "status": "joined",
            "node_id": node_id,
            "role": "worker",
            "endpoint": endpoint,
            "port": port,
            "pid": proc.pid,
            "join_response": result,
        })
    except Exception as e:
        return json.dumps({"error": str(e)})


def handle_cluster_submit(args: dict, **kwargs) -> str:
    base_url = _cluster_config.get("base_url")
    if not base_url:
        return json.dumps({"error": "Not connected to cluster. Run kanban_cluster_init or kanban_cluster_join first."})
    result = _api_call(base_url, "POST", "/api/v1/tasks", {
        "title": args.get("title", "Untitled task"),
        "requires": args.get("requires", []),
    })
    return json.dumps(result)


def handle_cluster_list(args: dict, **kwargs) -> str:
    base_url = _cluster_config.get("base_url")
    if not base_url:
        return json.dumps({"error": "Not connected to cluster."})
    result = _api_call(base_url, "GET", "/api/v1/tasks")
    return json.dumps(result, indent=2)


def handle_cluster_nodes(args: dict, **kwargs) -> str:
    base_url = _cluster_config.get("base_url")
    if not base_url:
        return json.dumps({"error": "Not connected to cluster."})
    result = _api_call(base_url, "GET", "/api/v1/nodes")
    return json.dumps(result, indent=2)


def handle_cluster_heartbeat(args: dict, **kwargs) -> str:
    base_url = _cluster_config.get("base_url")
    node_id = _cluster_config.get("node_id")
    if not base_url or not node_id:
        return json.dumps({"error": "Not connected to cluster."})
    result = _api_call(base_url, "POST", "/api/v1/nodes/heartbeat", {
        "node_id": node_id,
    })
    return json.dumps(result)


def handle_cluster_complete(args: dict, **kwargs) -> str:
    base_url = _cluster_config.get("base_url")
    node_id = _cluster_config.get("node_id")
    if not base_url:
        return json.dumps({"error": "Not connected to cluster."})
    task_id = args.get("task_id")
    if not task_id:
        return json.dumps({"error": "task_id is required"})
    result = _api_call(base_url, "POST", f"/api/v1/tasks/{task_id}/complete", {
        "node_id": node_id,
        "result": args.get("result", "completed"),
    })
    return json.dumps(result)


# ---------------------------------------------------------------------------
# Tool schemas
# ---------------------------------------------------------------------------

CLUSTER_INIT_SCHEMA = {
    "name": "kanban_cluster_init",
    "description": "Initialize a new hermes-agent-cluster cluster. This node becomes the main/coordinator node.",
    "parameters": {
        "type": "object",
        "properties": {
            "port": {"type": "integer", "description": "Port to listen on", "default": 8787},
            "node_id": {"type": "string", "description": "This node's unique ID"},
            "cluster_id": {"type": "string", "description": "Cluster identifier"},
            "capabilities": {"type": "array", "items": {"type": "string"}, "description": "Node capabilities"},
            "token": {"type": "string", "description": "Cluster auth token"},
        },
    },
}

CLUSTER_JOIN_SCHEMA = {
    "name": "kanban_cluster_join",
    "description": "Join an existing hermes-agent-cluster cluster as a worker node.",
    "parameters": {
        "type": "object",
        "properties": {
            "endpoint": {"type": "string", "description": "Main node URL (e.g. http://main:8787)"},
            "node_id": {"type": "string", "description": "This node's unique ID"},
            "port": {"type": "integer", "description": "Port to listen on", "default": 8788},
            "cluster_id": {"type": "string", "description": "Cluster identifier"},
            "capabilities": {"type": "array", "items": {"type": "string"}, "description": "Node capabilities"},
            "token": {"type": "string", "description": "Cluster auth token"},
        },
        "required": ["endpoint"],
    },
}

CLUSTER_SUBMIT_SCHEMA = {
    "name": "kanban_cluster_submit",
    "description": "Submit a task to the cluster for distributed execution.",
    "parameters": {
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "Task title/description"},
            "requires": {"type": "array", "items": {"type": "string"}, "description": "Required capabilities"},
        },
        "required": ["title"],
    },
}

CLUSTER_LIST_SCHEMA = {
    "name": "kanban_cluster_list",
    "description": "List all tasks in the cluster.",
    "parameters": {"type": "object", "properties": {}},
}

CLUSTER_NODES_SCHEMA = {
    "name": "kanban_cluster_nodes",
    "description": "List all nodes in the cluster.",
    "parameters": {"type": "object", "properties": {}},
}

CLUSTER_HEARTBEAT_SCHEMA = {
    "name": "kanban_cluster_heartbeat",
    "description": "Send heartbeat to the cluster to indicate this node is alive.",
    "parameters": {"type": "object", "properties": {}},
}

CLUSTER_COMPLETE_SCHEMA = {
    "name": "kanban_cluster_complete",
    "description": "Mark a task as completed with results.",
    "parameters": {
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "Task ID to complete"},
            "result": {"type": "string", "description": "Task result/description"},
        },
        "required": ["task_id"],
    },
}


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register kanban cluster tools with Hermes Agent."""
    # Try to ensure binary is available
    _find_or_install_binary()

    ctx.register_tool(
        name="kanban_cluster_init",
        toolset="kanban_cluster",
        schema=CLUSTER_INIT_SCHEMA,
        handler=handle_cluster_init,
        description="Initialize a distributed kanban cluster",
        emoji="🏗️",
    )
    ctx.register_tool(
        name="kanban_cluster_join",
        toolset="kanban_cluster",
        schema=CLUSTER_JOIN_SCHEMA,
        handler=handle_cluster_join,
        description="Join an existing kanban cluster",
        emoji="🔗",
    )
    ctx.register_tool(
        name="kanban_cluster_submit",
        toolset="kanban_cluster",
        schema=CLUSTER_SUBMIT_SCHEMA,
        handler=handle_cluster_submit,
        description="Submit a task to the cluster",
        emoji="📋",
    )
    ctx.register_tool(
        name="kanban_cluster_list",
        toolset="kanban_cluster",
        schema=CLUSTER_LIST_SCHEMA,
        handler=handle_cluster_list,
        description="List cluster tasks",
        emoji="📊",
    )
    ctx.register_tool(
        name="kanban_cluster_nodes",
        toolset="kanban_cluster",
        schema=CLUSTER_NODES_SCHEMA,
        handler=handle_cluster_nodes,
        description="List cluster nodes",
        emoji="🖥️",
    )
    ctx.register_tool(
        name="kanban_cluster_heartbeat",
        toolset="kanban_cluster",
        schema=CLUSTER_HEARTBEAT_SCHEMA,
        handler=handle_cluster_heartbeat,
        description="Send cluster heartbeat",
        emoji="💓",
    )
    ctx.register_tool(
        name="kanban_cluster_complete",
        toolset="kanban_cluster",
        schema=CLUSTER_COMPLETE_SCHEMA,
        handler=handle_cluster_complete,
        description="Complete a cluster task",
        emoji="✅",
    )

    logger.info("hermes-agent-cluster plugin: 7 tools registered")


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------

def on_session_start(ctx) -> None:
    """Ensure binary is available when a session starts."""
    binary = _find_or_install_binary()
    if binary:
        logger.info("hermes-cluster binary ready: %s", binary)
    else:
        logger.warning(
            "hermes-cluster not installed. Install it: bash %s/install.sh",
            _get_plugin_dir(),
        )
