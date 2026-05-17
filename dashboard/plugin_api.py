"""
hermes-agent-cluster dashboard plugin — backend API routes.

Mounts at /api/plugins/agent-cluster/ via the Hermes Dashboard plugin system.

REWRITE: Replaces HTTP proxy to Go service with direct FastAPI route calls
against the in-memory ClusterState. Config management (YAML read/write) is
retained as-is since it operates on local files.

Changes from v1:
  - Removed _proxy() HTTP helper — all cluster data comes from ClusterState
  - Added _state module-level reference, initialized via init()
  - Kept config management (YAML file I/O) unchanged
  - Same API surface for dashboard frontend compatibility
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, List, Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter()

# Cluster state — set via init() before mounting the router
_state = None  # ClusterState instance

# Config file paths (checked in order)
_CONFIG_PATHS = [
    Path.home() / ".hermes" / "agent-cluster" / "cluster.yaml",
    Path.home() / ".hermes" / "agent-cluster" / "cluster-worker.yaml",
]


def init(state) -> None:
    """Initialize the plugin API with a ClusterState instance.

    Called by the app factory or plugin loader before the router is mounted.
    """
    global _state
    _state = state
    logger.info("plugin_api initialized with cluster state")


def _get_state():
    """Get the cluster state, raising if not initialized."""
    if _state is None:
        raise HTTPException(status_code=503, detail="Cluster state not initialized")
    return _state


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _read_config_file() -> tuple[Optional[dict], Optional[str], Optional[str]]:
    """Read cluster.yaml, return (parsed_dict, raw_yaml, path)."""
    for p in _CONFIG_PATHS:
        if p.exists():
            try:
                raw = p.read_text(encoding="utf-8")
                parsed = yaml.safe_load(raw) or {}
                return parsed, raw, str(p)
            except Exception as e:
                logger.warning("Failed to read %s: %s", p, e)
                continue
    return None, None, None


def _write_config_file(cfg: dict) -> str:
    """Write config dict to the first available path, creating dir if needed."""
    path = _CONFIG_PATHS[0]
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = yaml.dump(cfg, default_flow_style=False, allow_unicode=True)
    path.write_text(raw, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# Config CRUD
# ---------------------------------------------------------------------------


class EndpointBody(BaseModel):
    endpoint: str


@router.post("/config")
async def set_endpoint(body: EndpointBody):
    """Set the cluster endpoint URL (for backward compat — now a no-op since
    we call ClusterState directly, but kept for dashboard API compatibility)."""
    logger.info("Cluster endpoint set to %s (direct mode, proxy ignored)", body.endpoint)
    return {"ok": True, "endpoint": body.endpoint, "mode": "direct"}


@router.get("/config")
async def get_endpoint():
    """Get the current cluster endpoint. In direct mode, returns the state info."""
    state = _get_state()
    return {
        "ok": True,
        "endpoint": "direct",
        "mode": "direct",
        "cluster_id": state.cluster_id,
        "node_id": state.node_id,
    }


@router.get("/config/node")
async def get_node_config():
    """Get node configuration from config file and runtime."""
    state = _get_state()
    cfg, raw_yaml, cfg_path = _read_config_file()

    result = {
        "ok": True,
        "config_file": cfg_path,
        "cluster": None,
        "node": None,
        "server": None,
        "lease": None,
        "watchdog": None,
        "telemetry": None,
    }

    if cfg:
        result["cluster"] = cfg.get("cluster", {})
        result["node"] = cfg.get("node", {})
        result["server"] = cfg.get("server", {})
        result["lease"] = cfg.get("lease", {})
        result["watchdog"] = cfg.get("watchdog", {})
        result["telemetry"] = cfg.get("telemetry", {})

    # Get runtime node info directly from state
    node_id = (result.get("node") or {}).get("id", state.node_id)
    node = state.get_node(node_id)
    if node:
        result["runtime"] = {
            "id": node.id,
            "name": node.name,
            "status": node.status.value if hasattr(node.status, "value") else node.status,
            "capabilities": node.capabilities,
            "load": node.load,
        }
    else:
        result["runtime"] = None

    return result


class CapabilitiesBody(BaseModel):
    capabilities: List[str]


@router.put("/config/capabilities")
async def update_capabilities(body: CapabilitiesBody):
    """Update node capabilities at runtime AND persist to config file."""
    state = _get_state()
    cfg, raw_yaml, cfg_path = _read_config_file()
    if not cfg:
        raise HTTPException(status_code=404, detail="Config file not found")

    node_id = (cfg.get("node") or {}).get("id", state.node_id)
    caps = body.capabilities

    # 1. Update in config file
    if "node" not in cfg:
        cfg["node"] = {}
    cfg["node"]["capabilities"] = caps
    saved_path = _write_config_file(cfg)
    logger.info("Saved capabilities to %s: %s", saved_path, caps)

    # 2. Update at runtime via ClusterState (direct call, no HTTP proxy)
    runtime_result = None
    try:
        state.update_capabilities(node_id, caps)
        # Re-trigger scheduling
        state.trigger_pending_tasks()
        state.schedule_pending()
        runtime_result = {
            "node_id": node_id,
            "capabilities": caps,
            "status": "updated",
        }
        logger.info("Runtime capability update applied directly")
    except Exception as e:
        runtime_result = {"warning": f"Runtime update failed: {e}"}

    return {
        "ok": True,
        "node_id": node_id,
        "capabilities": caps,
        "config_file": saved_path,
        "runtime": runtime_result,
    }


class NodeConfigBody(BaseModel):
    """Update persistent node config (requires restart to take full effect)."""
    name: Optional[str] = None
    capabilities: Optional[List[str]] = None


@router.put("/config/node")
async def update_node_config(body: NodeConfigBody):
    """Update node identity in config file (requires restart for most fields)."""
    state = _get_state()
    cfg, raw_yaml, cfg_path = _read_config_file()
    if not cfg:
        raise HTTPException(status_code=404, detail="Config file not found")

    if "node" not in cfg:
        cfg["node"] = {}

    changed = []
    if body.name is not None:
        cfg["node"]["name"] = body.name
        changed.append("name")
    if body.capabilities is not None:
        cfg["node"]["capabilities"] = body.capabilities
        changed.append("capabilities")

    saved_path = _write_config_file(cfg)

    # Runtime capability update if capabilities changed (direct call)
    runtime_result = None
    if "capabilities" in changed:
        node_id = cfg["node"].get("id", state.node_id)
        try:
            state.update_capabilities(node_id, cfg["node"]["capabilities"])
            state.trigger_pending_tasks()
            state.schedule_pending()
            runtime_result = {
                "node_id": node_id,
                "capabilities": cfg["node"]["capabilities"],
                "status": "updated",
            }
        except Exception as e:
            runtime_result = {"warning": f"Runtime update failed: {e}"}

    return {
        "ok": True,
        "changed": changed,
        "config_file": saved_path,
        "runtime": runtime_result,
        "needs_restart": [f for f in changed if f != "capabilities"],
    }


@router.get("/config/yaml")
async def get_config_yaml():
    """Get full config as raw YAML string."""
    cfg, raw_yaml, cfg_path = _read_config_file()
    return {
        "ok": True,
        "config_file": cfg_path,
        "yaml": raw_yaml or "",
    }


@router.get("/config/validate")
async def validate_config():
    """Validate the current cluster configuration."""
    cfg, _, _ = _read_config_file()
    errors = []
    warnings = []

    # Check required fields
    if not cfg:
        errors.append("No configuration file found")
    else:
        cluster = cfg.get("cluster", {})
        node = cfg.get("node", {})
        server = cfg.get("server", {})

        if not cluster.get("id"):
            warnings.append("cluster.id not set (using default)")
        if not node.get("id"):
            warnings.append("node.id not set")
        if not node.get("name"):
            warnings.append("node.name not set")
        port = server.get("port", 8787)
        if not isinstance(port, int) or port < 1 or port > 65535:
            errors.append(f"server.port must be 1-65535, got {port}")

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


class YamlBody(BaseModel):
    yaml: str


@router.put("/config/yaml")
async def save_config_yaml(body: YamlBody):
    """Save full config YAML (requires restart to take effect)."""
    try:
        parsed = yaml.safe_load(body.yaml)
        if not isinstance(parsed, dict):
            raise ValueError("Config must be a YAML mapping")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid YAML: {e}")

    saved_path = _write_config_file(parsed)
    return {"ok": True, "config_file": saved_path, "needs_restart": True}


@router.post("/config/restart")
async def restart_service():
    """Attempt to restart the hermes-cluster service.

    In direct mode, this is a no-op since the cluster runs in-process.
    Returns success with a note that restart is not needed.
    """
    return {
        "ok": True,
        "mode": "direct",
        "message": "Running in direct mode — restart not required",
    }


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get("/health")
async def health():
    """Check if the cluster state is available."""
    try:
        state = _get_state()
        nodes = state.get_all_nodes()
        return {"ok": True, "nodes": len(nodes), "mode": "direct"}
    except HTTPException:
        return {"ok": False, "error": "Cluster state not initialized"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# Cluster Data — direct ClusterState calls (no HTTP proxy)
# ---------------------------------------------------------------------------


@router.get("/nodes")
async def list_nodes():
    """List all cluster nodes."""
    state = _get_state()
    nodes = state.get_all_nodes()
    # Serialize to dict list for JSON response
    return [
        {
            "id": n.id,
            "name": n.name,
            "capabilities": n.capabilities,
            "status": n.status.value if hasattr(n.status, "value") else n.status,
            "last_heartbeat": n.last_heartbeat.isoformat() if n.last_heartbeat else "",
            "load": n.load,
        }
        for n in nodes
    ]


@router.get("/tasks")
async def list_tasks():
    """List all cluster tasks."""
    state = _get_state()
    tasks = state.get_all_tasks()
    return [
        {
            "id": t.id,
            "title": t.title,
            "requires": t.requires,
            "depends_on": t.depends_on,
            "priority": t.priority,
            "status": t.status.value if hasattr(t.status, "value") else t.status,
            "assigned_to": t.assigned_to,
            "created_at": t.created_at.isoformat() if t.created_at else "",
            "updated_at": t.updated_at.isoformat() if t.updated_at else "",
            "version": t.version,
            "fail_reason": t.fail_reason,
        }
        for t in tasks
    ]


@router.get("/leases")
async def list_leases():
    """List all active leases."""
    state = _get_state()
    leases = state.get_active_leases()
    return [
        {
            "id": l.id,
            "task_id": l.task_id,
            "node_id": l.node_id,
            "created_at": l.created_at.isoformat() if l.created_at else "",
            "expires_at": l.expires_at.isoformat() if l.expires_at else "",
            "status": l.status.value if hasattr(l.status, "value") else l.status,
        }
        for l in leases
    ]


@router.get("/status")
async def get_status(
    node: str = "",
    status: str = "",
    capability: str = "",
):
    """Get global cluster status view with optional filters."""
    state = _get_state()
    tasks = state.get_all_tasks()
    nodes = state.get_all_nodes()

    # Build entries matching Go's status view
    entries = []
    for task in tasks:
        entry = {
            "task_id": task.id,
            "title": task.title,
            "status": task.status.value if hasattr(task.status, "value") else task.status,
            "priority": task.priority,
            "assigned_to": task.assigned_to,
            "requires": task.requires,
        }
        # Filter
        if node and task.assigned_to != node:
            continue
        if status and entry["status"] != status:
            continue
        if capability and capability not in task.requires:
            continue
        entries.append(entry)

    # Build summary
    task_counts = state.task_counts()
    summary = {
        "total_tasks": task_counts["total"],
        "pending": task_counts.get("pending", 0),
        "ready": task_counts.get("ready", 0),
        "running": task_counts.get("running", 0),
        "completed": task_counts.get("completed", 0),
        "failed": task_counts.get("failed", 0),
        "total_nodes": state.node_count(),
        "online_nodes": state.online_count(),
    }

    return {"entries": entries, "summary": summary}


@router.get("/topology")
async def get_topology():
    """Get cluster topology."""
    state = _get_state()
    nodes = state.get_all_nodes()
    return {
        "nodes": [
            {
                "id": n.id,
                "name": n.name,
                "status": n.status.value if hasattr(n.status, "value") else n.status,
                "capabilities": n.capabilities,
                "load": n.load,
            }
            for n in nodes
        ],
    }


@router.get("/cluster-metrics")
async def get_cluster_metrics():
    """Get aggregated cluster metrics."""
    state = _get_state()
    task_counts = state.task_counts()
    return {
        "nodes": {"total": state.node_count(), "online": state.online_count()},
        "tasks": task_counts,
        "sync_version": state.sync_version(),
    }


@router.get("/timeline")
async def get_timeline():
    """Get cluster event timeline."""
    state = _get_state()
    events = state.get_recovery_events()
    # Return most recent 50 events
    return events[-50:] if len(events) > 50 else events


@router.get("/workflow/graph")
async def get_workflow_graph():
    """Get workflow dependency graph."""
    state = _get_state()
    return state.get_workflow_graph()
