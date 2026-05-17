"""
hermes-agent-cluster plugin — Distributed Hermes cluster coordination.

Pure Python implementation. No Go binary needed.
Registers 7 kanban_cluster_* tools for multi-node task orchestration.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PLUGIN_NAME = "hermes-agent-cluster"

# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

_core = None  # ClusterCore instance


def _get_core():
    """Lazy-init ClusterCore (SQLite-backed, runs in-process)."""
    global _core
    if _core is not None:
        return _core

    from hermes_cluster.state.cluster_store import ClusterStore
    from hermes_cluster.core.cluster_core import ClusterCore

    db_path = os.path.expanduser("~/.hermes/agent-cluster/cluster.db")
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    store = ClusterStore(db_path)
    _core = ClusterCore(store)
    _core.start()
    logger.info("ClusterCore initialized: db=%s", db_path)
    return _core


def _to_json(obj: Any) -> str:
    """Serialize to JSON string, handling Pydantic models and datetimes."""
    if hasattr(obj, "dict"):
        return json.dumps(obj.dict(), default=str, ensure_ascii=False)
    if hasattr(obj, "__dict__"):
        return json.dumps(
            {k: v for k, v in obj.__dict__.items() if not k.startswith("_")},
            default=str,
            ensure_ascii=False,
        )
    return json.dumps(obj, default=str, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Tool Schemas
# ---------------------------------------------------------------------------

CLUSTER_INIT_SCHEMA = {
    "type": "object",
    "properties": {
        "cluster_id": {"type": "string", "description": "Cluster identifier"},
        "role": {
            "type": "string",
            "enum": ["main", "worker"],
            "description": "Node role",
            "default": "main",
        },
        "node_name": {"type": "string", "description": "Name for this node"},
        "capabilities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Node capabilities (e.g. coding, gpu, browser)",
            "default": [],
        },
        "port": {
            "type": "integer",
            "description": "Port to listen on",
            "default": 8787,
        },
    },
    "required": ["node_name"],
}

CLUSTER_JOIN_SCHEMA = {
    "type": "object",
    "properties": {
        "endpoint": {
            "type": "string",
            "description": "Main node URL (e.g. http://192.168.1.100:8787)",
        },
        "token": {"type": "string", "description": "Cluster join token"},
        "node_name": {"type": "string", "description": "Name for this worker node"},
        "capabilities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Node capabilities",
            "default": [],
        },
    },
    "required": ["endpoint", "node_name"],
}

CLUSTER_SUBMIT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "Task title"},
        "body": {"type": "string", "description": "Task description (ignored, for future use)", "default": ""},
        "priority": {
            "type": "integer",
            "description": "Task priority (1=highest, 5=lowest)",
            "default": 3,
        },
        "required_capabilities": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Required node capabilities",
            "default": [],
        },
        "dependencies": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Task IDs this task depends on",
            "default": [],
        },
    },
    "required": ["title"],
}

CLUSTER_LIST_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {
            "type": "string",
            "description": "Filter by status (pending/ready/running/completed/failed)",
        },
        "limit": {
            "type": "integer",
            "description": "Max results",
            "default": 20,
        },
    },
}

CLUSTER_NODES_SCHEMA = {"type": "object", "properties": {}}

CLUSTER_HEARTBEAT_SCHEMA = {
    "type": "object",
    "properties": {
        "node_id": {"type": "string", "description": "Node ID to heartbeat"},
        "load_score": {
            "type": "number",
            "description": "Current load (0.0-1.0)",
            "default": 0.0,
        },
    },
    "required": ["node_id"],
}

CLUSTER_COMPLETE_SCHEMA = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string", "description": "Task ID to complete"},
        "result": {
            "type": "string",
            "description": "Task result/output",
            "default": "",
        },
    },
    "required": ["task_id"],
}


# ---------------------------------------------------------------------------
# Tool Handlers
# ---------------------------------------------------------------------------

def handle_cluster_init(args: dict, **kwargs) -> str:
    """Initialize a distributed kanban cluster (creates this node)."""
    try:
        from hermes_cluster.models import Node, NodeStatus

        core = _get_core()
        node_name = args.get("node_name", "main-node")
        role = args.get("role", "main")
        capabilities = args.get("capabilities", [])

        node = Node(
            id=f"node_{node_name}",
            name=node_name,
            capabilities=capabilities,
            status=NodeStatus.online,
        )
        core.store.register_node(node)

        # Save config
        cfg = core.store.get_config() or {}
        cfg["cluster_id"] = args.get("cluster_id", "hermes-cluster")
        cfg["node_id"] = node.id
        cfg["node_role"] = role
        core.store.set_config(cfg)

        return _to_json({
            "ok": True,
            "node_id": node.id,
            "node_name": node.name,
            "role": role,
            "capabilities": capabilities,
            "message": f"Cluster initialized. Node '{node_name}' registered as {role}.",
        })
    except Exception as e:
        logger.exception("cluster_init failed")
        return json.dumps({"ok": False, "error": str(e)})


def handle_cluster_join(args: dict, **kwargs) -> str:
    """Join an existing kanban cluster as a worker node."""
    try:
        from hermes_cluster.models import Node, NodeStatus

        core = _get_core()
        node_name = args.get("node_name", "worker-node")
        capabilities = args.get("capabilities", [])
        endpoint = args.get("endpoint", "")

        node = Node(
            id=f"node_{node_name}",
            name=node_name,
            capabilities=capabilities,
            status=NodeStatus.online,
        )
        core.store.register_node(node)

        cfg = core.store.get_config() or {}
        cfg["cluster_endpoint"] = endpoint
        cfg["node_id"] = node.id
        cfg["node_role"] = "worker"
        core.store.set_config(cfg)

        return _to_json({
            "ok": True,
            "node_id": node.id,
            "node_name": node.name,
            "role": "worker",
            "endpoint": endpoint,
            "message": f"Joined cluster. Node '{node_name}' registered as worker.",
        })
    except Exception as e:
        logger.exception("cluster_join failed")
        return json.dumps({"ok": False, "error": str(e)})


def handle_cluster_submit(args: dict, **kwargs) -> str:
    """Submit a task to the cluster (auto-schedules to matching node)."""
    try:
        core = _get_core()

        # Set dependencies if provided
        dependencies = args.get("dependencies", [])

        task = core.submit_task(
            title=args["title"],
            requires=args.get("required_capabilities", []),
            priority=args.get("priority", 3),
        )

        # Set dependencies after creation (if any)
        if dependencies:
            core.store.set_dependencies(task.id, dependencies)

        return _to_json({
            "ok": True,
            "task_id": task.id,
            "title": task.title,
            "status": task.status.value if hasattr(task.status, "value") else str(task.status),
            "assigned_to": task.assigned_to,
            "message": f"Task submitted: {task.title}",
        })
    except Exception as e:
        logger.exception("cluster_submit failed")
        return json.dumps({"ok": False, "error": str(e)})


def handle_cluster_list(args: dict, **kwargs) -> str:
    """List cluster tasks with optional status filter."""
    try:
        core = _get_core()
        status_filter = args.get("status")
        limit = args.get("limit", 20)

        tasks = core.store.get_all_tasks()
        if status_filter:
            tasks = [t for t in tasks if t.status.value == status_filter]

        result = []
        for t in tasks[:limit]:
            result.append({
                "id": t.id,
                "title": t.title,
                "status": t.status.value if hasattr(t.status, "value") else str(t.status),
                "assigned_to": t.assigned_to,
                "requires": t.requires,
                "priority": t.priority,
            })
        return _to_json({"ok": True, "tasks": result, "total": len(tasks)})
    except Exception as e:
        logger.exception("cluster_list failed")
        return json.dumps({"ok": False, "error": str(e)})


def handle_cluster_nodes(args: dict, **kwargs) -> str:
    """List all cluster nodes with status and capabilities."""
    try:
        core = _get_core()
        nodes = core.store.get_all_nodes()
        result = []
        for n in nodes:
            result.append({
                "id": n.id,
                "name": n.name,
                "status": n.status.value if hasattr(n.status, "value") else str(n.status),
                "capabilities": n.capabilities,
                "load": n.load,
            })
        return _to_json({"ok": True, "nodes": result, "total": len(nodes)})
    except Exception as e:
        logger.exception("cluster_nodes failed")
        return json.dumps({"ok": False, "error": str(e)})


def handle_cluster_heartbeat(args: dict, **kwargs) -> str:
    """Send a heartbeat to keep the node online."""
    try:
        core = _get_core()
        node_id = args["node_id"]

        core.store.update_heartbeat(node_id)
        return _to_json({
            "ok": True,
            "node_id": node_id,
            "message": "Heartbeat recorded.",
        })
    except Exception as e:
        logger.exception("cluster_heartbeat failed")
        return json.dumps({"ok": False, "error": str(e)})


def handle_cluster_complete(args: dict, **kwargs) -> str:
    """Mark a task as completed (triggers dependent tasks)."""
    try:
        core = _get_core()
        task_id = args["task_id"]

        ok = core.complete_task(task_id)
        if ok:
            return _to_json({
                "ok": True,
                "task_id": task_id,
                "message": "Task completed. Dependent tasks have been triggered.",
            })
        else:
            return _to_json({
                "ok": False,
                "task_id": task_id,
                "error": "Task not found or not in completable state.",
            })
    except Exception as e:
        logger.exception("cluster_complete failed")
        return json.dumps({"ok": False, "error": str(e)})


# ---------------------------------------------------------------------------
# Plugin Registration
# ---------------------------------------------------------------------------

def register(ctx) -> None:
    """Register kanban cluster tools with Hermes Agent."""
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
        emoji="📋",
    )
    ctx.register_tool(
        name="kanban_cluster_nodes",
        toolset="kanban_cluster",
        schema=CLUSTER_NODES_SCHEMA,
        handler=handle_cluster_nodes,
        description="List cluster nodes and their capabilities",
        emoji="🖥️",
    )
    ctx.register_tool(
        name="kanban_cluster_heartbeat",
        toolset="kanban_cluster",
        schema=CLUSTER_HEARTBEAT_SCHEMA,
        handler=handle_cluster_heartbeat,
        description="Send a heartbeat to keep the node online",
        emoji="💓",
    )
    ctx.register_tool(
        name="kanban_cluster_complete",
        toolset="kanban_cluster",
        schema=CLUSTER_COMPLETE_SCHEMA,
        handler=handle_cluster_complete,
        description="Mark a task as completed",
        emoji="✅",
    )

    logger.info("hermes-agent-cluster plugin: 7 tools registered (pure Python, no Go binary)")


# ---------------------------------------------------------------------------
# Plugin Lifecycle
# ---------------------------------------------------------------------------

def on_session_start(ctx) -> None:
    """Ensure ClusterCore is initialized when a session starts."""
    try:
        core = _get_core()
        summary = core.get_summary()
        logger.info(
            "ClusterCore ready: %d nodes, %d tasks",
            summary.get("total_nodes", 0),
            summary.get("total_tasks", 0),
        )
    except Exception as e:
        logger.warning("ClusterCore init failed: %s", e)


def on_gateway_startup(ctx) -> None:
    """Initialize ClusterCore on gateway startup."""
    try:
        core = _get_core()
        # Also initialize the dashboard API
        try:
            from dashboard.plugin_api import init as init_dashboard
            init_dashboard(core.store)
            logger.info("Dashboard API initialized with ClusterStore")
        except ImportError:
            pass
        logger.info("hermes-agent-cluster: ClusterCore started on gateway startup")
    except Exception as e:
        logger.warning("hermes-agent-cluster: startup init failed: %s", e)


def on_session_end(ctx) -> None:
    """Clean up on session end."""
    global _core
    if _core is not None:
        try:
            _core.stop()
        except Exception:
            pass
        _core = None
