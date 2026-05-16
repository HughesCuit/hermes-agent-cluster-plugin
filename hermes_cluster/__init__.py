"""hermes-agent-cluster plugin — Distributed Kanban cluster for Hermes Agent.

Registers tools that let the agent interact with a hermes-agent-cluster cluster:
- kanban_cluster_init: Initialize a new cluster (main node)
- kanban_cluster_join: Join a cluster as worker
- kanban_cluster_submit: Submit a task to the cluster
- kanban_cluster_list: List tasks on the cluster
- kanban_cluster_nodes: List cluster nodes
- kanban_cluster_heartbeat: Send heartbeat
- kanban_cluster_complete: Mark task as completed

V2: Pure Python implementation — no Go binary dependency.
Uses ClusterStore (SQLite) for persistent state and ClusterCore for business logic.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .models import (
    EventType,
    LeaseStatus,
    Node,
    NodeStatus,
    Task,
    TaskStatus,
)
from .state.cluster_store import ClusterStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton state
# ---------------------------------------------------------------------------

_store: Optional[ClusterStore] = None
_store_lock = threading.Lock()
_heartbeat_thread: Optional[threading.Thread] = None
_heartbeat_stop = threading.Event()
_config: Dict[str, Any] = {}

# Defaults
DEFAULT_DB_DIR = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "agent-cluster"
DEFAULT_DB_PATH = DEFAULT_DB_DIR / "cluster.db"
DEFAULT_HEARTBEAT_INTERVAL = 10  # seconds
DEFAULT_LEASE_TTL = 60  # seconds
DEFAULT_NODE_ID = f"node_{secrets.token_hex(4)}"
DEFAULT_CLUSTER_ID = "hermes-cluster"


# ---------------------------------------------------------------------------
# Store lifecycle
# ---------------------------------------------------------------------------

def _get_store() -> ClusterStore:
    """Get or initialize the global ClusterStore singleton."""
    global _store, _config

    with _store_lock:
        if _store is not None:
            return _store

        # Load configuration
        _config = _load_config()

        # Resolve DB path
        db_path = _config.get("db_path")
        if not db_path:
            db_dir = Path(_config.get("db_dir", str(DEFAULT_DB_DIR)))
            db_dir.mkdir(parents=True, exist_ok=True)
            db_path = str(db_dir / "cluster.db")

        # Initialize store
        _store = ClusterStore(db_path=db_path)
        _store.cluster_id = _config.get("cluster_id", DEFAULT_CLUSTER_ID)
        _store.node_id = _config.get("node_id", DEFAULT_NODE_ID)
        _store.node_role = _config.get("role", "main")

        # Store config in KV (node registration happens on init/join)
        _store.set_config(_config)

        logger.info(
            "ClusterStore initialized: db=%s, node=%s, role=%s",
            db_path, _store.node_id, _store.node_role,
        )
        return _store


def _load_config() -> Dict[str, Any]:
    """Load plugin configuration from environment and config file.

    Priority: env vars > config file > defaults.
    """
    config = {
        "auto_start": True,
        "cluster_id": DEFAULT_CLUSTER_ID,
        "node_id": DEFAULT_NODE_ID,
        "node_name": "main-node",
        "role": "main",
        "capabilities": ["planning", "reviewing", "scheduling"],
        "heartbeat_interval": DEFAULT_HEARTBEAT_INTERVAL,
        "lease_ttl": DEFAULT_LEASE_TTL,
        "db_path": "",
        "db_dir": str(DEFAULT_DB_DIR),
    }

    # Environment overrides
    env_map = {
        "HERMES_CLUSTER_AUTO_START": ("auto_start", lambda x: x.lower() in ("true", "1", "yes")),
        "HERMES_CLUSTER_ID": ("cluster_id", str),
        "HERMES_CLUSTER_NODE_ID": ("node_id", str),
        "HERMES_CLUSTER_NODE_NAME": ("node_name", str),
        "HERMES_CLUSTER_ROLE": ("role", str),
        "HERMES_CLUSTER_DB_PATH": ("db_path", str),
        "HERMES_CLUSTER_DB_DIR": ("db_dir", str),
        "HERMES_CLUSTER_HEARTBEAT_INTERVAL": ("heartbeat_interval", int),
        "HERMES_CLUSTER_LEASE_TTL": ("lease_ttl", int),
    }

    env_set_keys = set()
    for env_var, (key, converter) in env_map.items():
        value = os.environ.get(env_var)
        if value is not None:
            try:
                config[key] = converter(value)
                env_set_keys.add(key)
            except (ValueError, TypeError):
                logger.warning("Invalid value for %s: %s", env_var, value)

    # Capabilities from env (comma-separated)
    caps_env = os.environ.get("HERMES_CLUSTER_CAPABILITIES")
    if caps_env:
        config["capabilities"] = [c.strip() for c in caps_env.split(",") if c.strip()]
        env_set_keys.add("capabilities")

    # Plugin config file
    config_dir = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "agent-cluster"
    plugin_config_path = config_dir / "plugin.yaml"

    if plugin_config_path.exists():
        try:
            import yaml
            with open(plugin_config_path) as f:
                plugin_config = yaml.safe_load(f)
                if isinstance(plugin_config, dict):
                    for key, value in plugin_config.items():
                        if key in config and key not in env_set_keys:
                            config[key] = value
        except Exception as e:
            logger.debug("Failed to load plugin config: %s", e)

    return config


def _ensure_store() -> ClusterStore:
    """Ensure the store is initialized; returns it or raises."""
    store = _get_store()
    if store is None:
        raise RuntimeError("ClusterStore not initialized")
    return store


# ---------------------------------------------------------------------------
# Heartbeat background thread
# ---------------------------------------------------------------------------

def _heartbeat_loop():
    """Background thread that sends heartbeats periodically."""
    interval = _config.get("heartbeat_interval", DEFAULT_HEARTBEAT_INTERVAL)
    while not _heartbeat_stop.is_set():
        try:
            store = _get_store()
            if store:
                store.update_heartbeat(store.node_id)
        except Exception as e:
            logger.debug("Heartbeat error: %s", e)
        _heartbeat_stop.wait(timeout=interval)


def _start_heartbeat():
    """Start the background heartbeat thread."""
    global _heartbeat_thread
    if _heartbeat_thread and _heartbeat_thread.is_alive():
        return
    _heartbeat_stop.clear()
    _heartbeat_thread = threading.Thread(
        target=_heartbeat_loop, daemon=True, name="cluster-heartbeat"
    )
    _heartbeat_thread.start()
    logger.debug("Heartbeat thread started")


def _stop_heartbeat():
    """Stop the background heartbeat thread."""
    _heartbeat_stop.set()
    if _heartbeat_thread and _heartbeat_thread.is_alive():
        _heartbeat_thread.join(timeout=3)
    logger.debug("Heartbeat thread stopped")


# ---------------------------------------------------------------------------
# Tool handlers
# ---------------------------------------------------------------------------

def handle_cluster_init(args: dict, **kwargs) -> str:
    """Initialize a new cluster (main node)."""
    try:
        store = _ensure_store()

        # Override config from args
        if "port" in args:
            _config["port"] = args["port"]
        if "node_id" in args:
            _config["node_id"] = args["node_id"]
            store.node_id = args["node_id"]
        if "cluster_id" in args:
            _config["cluster_id"] = args["cluster_id"]
            store.cluster_id = args["cluster_id"]
        if "capabilities" in args:
            _config["capabilities"] = args["capabilities"]
        if "token" in args:
            _config["token"] = args["token"]

        # Update node registration
        node = Node(
            id=store.node_id,
            name=_config.get("node_name", store.node_id),
            capabilities=_config.get("capabilities", []),
            status=NodeStatus.online,
        )
        store.register_node(node)
        store.node_role = "main"
        _config["role"] = "main"
        store.set_config(_config)

        # Start heartbeat
        _start_heartbeat()

        summary = store.get_summary()
        return json.dumps({
            "status": "initialized",
            "node_id": store.node_id,
            "role": "main",
            "cluster_id": store.cluster_id,
            "summary": summary,
        })
    except Exception as e:
        logger.error("Cluster init failed: %s", e)
        return json.dumps({"error": str(e)})


def handle_cluster_join(args: dict, **kwargs) -> str:
    """Join an existing cluster as worker node."""
    try:
        store = _ensure_store()

        endpoint = args.get("endpoint", "")
        node_id = args.get("node_id", f"worker_{secrets.token_hex(4)}")
        capabilities = args.get("capabilities", ["coding", "gpu", "browser"])

        # Override config
        _config["node_id"] = node_id
        _config["role"] = "worker"
        _config["endpoint"] = endpoint
        _config["capabilities"] = capabilities
        store.node_id = node_id
        store.node_role = "worker"

        # Register this worker node
        node = Node(
            id=node_id,
            name=args.get("node_name", node_id),
            capabilities=capabilities,
            status=NodeStatus.online,
        )
        store.register_node(node)
        store.set_config(_config)

        # Start heartbeat
        _start_heartbeat()

        # If endpoint provided, try to register with main node via HTTP
        join_response = {}
        if endpoint:
            try:
                from urllib.request import Request, urlopen
                from urllib.error import URLError

                url = f"{endpoint}/api/v1/nodes/join"
                payload = json.dumps({
                    "node_name": node_id,
                    "capabilities": capabilities,
                    "endpoint": f"local://{node_id}",
                }).encode()
                req = Request(url, data=payload, method="POST")
                req.add_header("Content-Type", "application/json")
                with urlopen(req, timeout=10) as resp:
                    join_response = json.loads(resp.read().decode())
            except Exception as e:
                join_response = {"warning": f"Could not reach main node: {e}"}

        return json.dumps({
            "status": "joined",
            "node_id": node_id,
            "role": "worker",
            "endpoint": endpoint,
            "join_response": join_response,
        })
    except Exception as e:
        logger.error("Cluster join failed: %s", e)
        return json.dumps({"error": str(e)})


def handle_cluster_submit(args: dict, **kwargs) -> str:
    """Submit a task to the cluster for distributed execution."""
    try:
        store = _ensure_store()

        title = args.get("title")
        if not title:
            return json.dumps({"error": "title is required"})

        requires = args.get("requires", [])
        priority = args.get("priority", 3)

        # Generate task ID
        task_id = f"task_{secrets.token_hex(8)}"

        # Create task in store
        task = store.create_task(
            task_id=task_id,
            title=title,
            requires=requires,
            priority=priority,
        )

        # Trigger scheduling
        store.trigger_pending_tasks()
        scheduled = store.schedule_pending()

        return json.dumps({
            "task_id": task.id,
            "title": task.title,
            "status": task.status.value,
            "assigned_to": task.assigned_to,
            "priority": task.priority,
            "scheduled": scheduled > 0,
        })
    except Exception as e:
        logger.error("Cluster submit failed: %s", e)
        return json.dumps({"error": str(e)})


def handle_cluster_list(args: dict, **kwargs) -> str:
    """List all tasks in the cluster."""
    try:
        store = _ensure_store()

        tasks = store.get_all_tasks()
        counts = store.task_counts()

        result = []
        for task in tasks:
            result.append({
                "id": task.id,
                "title": task.title,
                "status": task.status.value,
                "assigned_to": task.assigned_to,
                "priority": task.priority,
                "requires": task.requires,
                "created_at": task.created_at.isoformat() if task.created_at else None,
            })

        return json.dumps({
            "tasks": result,
            "counts": counts,
        }, indent=2)
    except Exception as e:
        logger.error("Cluster list failed: %s", e)
        return json.dumps({"error": str(e)})


def handle_cluster_nodes(args: dict, **kwargs) -> str:
    """List all nodes in the cluster."""
    try:
        store = _ensure_store()

        nodes = store.get_all_nodes()
        result = []
        for node in nodes:
            result.append({
                "id": node.id,
                "name": node.name,
                "capabilities": node.capabilities,
                "status": node.status.value,
                "last_heartbeat": node.last_heartbeat.isoformat() if node.last_heartbeat else None,
                "load": node.load,
            })

        summary = store.get_summary()
        return json.dumps({
            "nodes": result,
            "summary": summary,
        }, indent=2)
    except Exception as e:
        logger.error("Cluster nodes failed: %s", e)
        return json.dumps({"error": str(e)})


def handle_cluster_heartbeat(args: dict, **kwargs) -> str:
    """Send heartbeat to the cluster to indicate this node is alive."""
    try:
        store = _ensure_store()

        node_id = args.get("node_id", store.node_id)
        store.update_heartbeat(node_id)

        node = store.get_node(node_id)
        return json.dumps({
            "node_id": node_id,
            "status": "ok",
            "last_heartbeat": node.last_heartbeat.isoformat() if node and node.last_heartbeat else None,
        })
    except Exception as e:
        logger.error("Cluster heartbeat failed: %s", e)
        return json.dumps({"error": str(e)})


def handle_cluster_complete(args: dict, **kwargs) -> str:
    """Mark a task as completed with results."""
    try:
        store = _ensure_store()

        task_id = args.get("task_id")
        if not task_id:
            return json.dumps({"error": "task_id is required"})

        task = store.get_task(task_id)
        if not task:
            return json.dumps({"error": f"Task {task_id} not found"})

        # Mark completed
        store.set_task_status(task_id, TaskStatus.completed)

        # Trigger dependent tasks
        promoted = store.trigger_pending_tasks()
        scheduled = store.schedule_pending()

        # Get updated task
        updated_task = store.get_task(task_id)

        return json.dumps({
            "task_id": task_id,
            "status": "completed",
            "promoted_dependencies": promoted,
            "newly_scheduled": scheduled,
        })
    except Exception as e:
        logger.error("Cluster complete failed: %s", e)
        return json.dumps({"error": str(e)})


# ---------------------------------------------------------------------------
# Tool schemas (unchanged API contract)
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
            "priority": {"type": "integer", "description": "Task priority (1=highest, 5=lowest)", "default": 3},
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
# Hook handlers
# ---------------------------------------------------------------------------

def _on_session_start(**kwargs) -> None:
    """Initialize cluster service when session begins."""
    config = _load_config()

    if not config.get("auto_start", True):
        logger.debug("Auto-start disabled, skipping cluster startup")
        return

    # Initialize store in background to avoid blocking session start
    def _init_in_background():
        try:
            _get_store()
            _start_heartbeat()
            logger.info("Cluster initialized (Python/SQLite)")
        except Exception as e:
            logger.warning("Cluster auto-init failed: %s", e)

    thread = threading.Thread(target=_init_in_background, daemon=True, name="cluster-init")
    thread.start()


def _on_session_end(**kwargs) -> None:
    """Gracefully stop cluster service when session ends."""
    _stop_heartbeat()

    with _store_lock:
        global _store
        if _store is not None:
            try:
                _store.close()
            except Exception:
                pass
            _store = None

    logger.info("Cluster plugin session ended")


def _on_gateway_startup(**kwargs) -> None:
    """Auto-start cluster when gateway process starts."""
    _on_session_start(**kwargs)


# ---------------------------------------------------------------------------
# Plugin registration
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

    # Register lifecycle hooks
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("on_session_end", _on_session_end)
    ctx.register_hook("on_gateway_startup", _on_gateway_startup)

    logger.info("hermes-agent-cluster plugin registered 7 cluster tools + lifecycle hooks (Python/SQLite)")
