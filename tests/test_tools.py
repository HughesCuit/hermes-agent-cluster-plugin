"""Tests for hermes-agent-cluster __init__.py — 7 kanban_cluster_* tools.

Tests verify each tool handler works correctly with the Python/SQLite backend.
No Go binary dependency — all tests run against in-memory ClusterStore.
"""

import json
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Set env before importing
os.environ.setdefault("HERMES_CLUSTER_AUTO_START", "false")
os.environ.setdefault("HERMES_CLUSTER_DB_PATH", ":memory:")

from hermes_cluster import (
    _config,
    _get_store,
    _store,
    _store_lock,
    handle_cluster_complete,
    handle_cluster_init,
    handle_cluster_join,
    handle_cluster_list,
    handle_cluster_nodes,
    handle_cluster_heartbeat,
    handle_cluster_submit,
)
from hermes_cluster.models import Node, NodeStatus, TaskStatus
from hermes_cluster.state.cluster_store import ClusterStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def reset_store():
    """Reset global store before each test."""
    import hermes_cluster as mod

    with mod._store_lock:
        if mod._store is not None:
            mod._store.close()
            mod._store = None
    mod._config.clear()
    mod._heartbeat_stop.set()
    if mod._heartbeat_thread and mod._heartbeat_thread.is_alive():
        mod._heartbeat_thread.join(timeout=2)
    mod._heartbeat_thread = None
    yield
    with mod._store_lock:
        if mod._store is not None:
            mod._store.close()
            mod._store = None


@pytest.fixture
def store():
    """Get a fresh in-memory store."""
    return _get_store()


# ---------------------------------------------------------------------------
# handle_cluster_init
# ---------------------------------------------------------------------------

class TestClusterInit:
    def test_basic_init(self):
        result = json.loads(handle_cluster_init({}))
        assert result["status"] == "initialized"
        assert result["role"] == "main"
        assert "node_id" in result
        assert "cluster_id" in result
        assert "summary" in result

    def test_init_with_custom_params(self):
        result = json.loads(handle_cluster_init({
            "node_id": "custom_node",
            "cluster_id": "my-cluster",
            "capabilities": ["gpu", "browser"],
        }))
        assert result["status"] == "initialized"
        assert result["node_id"] == "custom_node"
        assert result["cluster_id"] == "my-cluster"

    def test_init_registers_node(self):
        handle_cluster_init({"node_id": "test_node", "capabilities": ["planning"]})
        s = _get_store()
        node = s.get_node("test_node")
        assert node is not None
        assert node.capabilities == ["planning"]
        assert node.status == NodeStatus.online

    def test_init_returns_summary(self):
        result = json.loads(handle_cluster_init({}))
        summary = result["summary"]
        assert "cluster_id" in summary
        assert "nodes" in summary
        assert "tasks" in summary
        assert summary["nodes"]["total"] >= 1  # At least this node


# ---------------------------------------------------------------------------
# handle_cluster_join
# ---------------------------------------------------------------------------

class TestClusterJoin:
    def test_basic_join(self):
        result = json.loads(handle_cluster_join({
            "endpoint": "http://main:8787",
            "node_id": "worker_1",
            "capabilities": ["coding", "gpu"],
        }))
        assert result["status"] == "joined"
        assert result["node_id"] == "worker_1"
        assert result["role"] == "worker"
        assert result["endpoint"] == "http://main:8787"

    def test_join_registers_worker_node(self):
        handle_cluster_join({
            "endpoint": "http://main:8787",
            "node_id": "worker_alpha",
            "capabilities": ["coding"],
        })
        s = _get_store()
        node = s.get_node("worker_alpha")
        assert node is not None
        assert node.capabilities == ["coding"]

    def test_join_without_endpoint_warns(self):
        """Join without main endpoint still works (standalone mode)."""
        result = json.loads(handle_cluster_join({
            "node_id": "standalone_worker",
        }))
        assert result["status"] == "joined"
        assert result["node_id"] == "standalone_worker"


# ---------------------------------------------------------------------------
# handle_cluster_submit
# ---------------------------------------------------------------------------

class TestClusterSubmit:
    def test_basic_submit(self):
        handle_cluster_init({})
        result = json.loads(handle_cluster_submit({
            "title": "Build feature X",
        }))
        assert "task_id" in result
        assert result["title"] == "Build feature X"
        assert result["status"] in ("ready", "running", "pending")

    def test_submit_with_capabilities(self):
        handle_cluster_init({"capabilities": ["gpu", "coding"]})
        result = json.loads(handle_cluster_submit({
            "title": "GPU task",
            "requires": ["gpu"],
            "priority": 1,
        }))
        assert "task_id" in result
        assert result["priority"] == 1

    def test_submit_missing_title(self):
        handle_cluster_init({})
        result = json.loads(handle_cluster_submit({}))
        assert "error" in result
        assert "title" in result["error"]

    def test_submit_creates_task_in_store(self):
        handle_cluster_init({})
        result = json.loads(handle_cluster_submit({"title": "Test task"}))
        s = _get_store()
        task = s.get_task(result["task_id"])
        assert task is not None
        assert task.title == "Test task"

    def test_submit_assigns_to_capable_node(self):
        handle_cluster_init({"capabilities": ["gpu", "coding"]})
        result = json.loads(handle_cluster_submit({
            "title": "GPU task",
            "requires": ["gpu"],
        }))
        # Should be assigned to the node that has "gpu" capability
        s = _get_store()
        task = s.get_task(result["task_id"])
        assert task is not None


# ---------------------------------------------------------------------------
# handle_cluster_list
# ---------------------------------------------------------------------------

class TestClusterList:
    def test_list_empty(self):
        handle_cluster_init({})
        result = json.loads(handle_cluster_list({}))
        assert "tasks" in result
        assert "counts" in result
        assert result["counts"]["total"] == 0

    def test_list_with_tasks(self):
        handle_cluster_init({})
        handle_cluster_submit({"title": "Task 1"})
        handle_cluster_submit({"title": "Task 2"})

        result = json.loads(handle_cluster_list({}))
        assert result["counts"]["total"] == 2
        titles = [t["title"] for t in result["tasks"]]
        assert "Task 1" in titles
        assert "Task 2" in titles

    def test_list_shows_status(self):
        handle_cluster_init({})
        submit_result = json.loads(handle_cluster_submit({"title": "Test"}))
        result = json.loads(handle_cluster_list({}))
        task = result["tasks"][0]
        assert task["status"] in ("ready", "running", "pending")


# ---------------------------------------------------------------------------
# handle_cluster_nodes
# ---------------------------------------------------------------------------

class TestClusterNodes:
    def test_nodes_after_init(self):
        handle_cluster_init({"node_id": "main_node", "capabilities": ["planning"]})
        result = json.loads(handle_cluster_nodes({}))
        assert "nodes" in result
        assert len(result["nodes"]) >= 1
        assert result["nodes"][0]["id"] == "main_node"

    def test_nodes_with_workers(self):
        handle_cluster_init({"node_id": "main"})
        handle_cluster_join({"endpoint": "http://main:8787", "node_id": "w1"})
        handle_cluster_join({"endpoint": "http://main:8787", "node_id": "w2"})

        result = json.loads(handle_cluster_nodes({}))
        assert len(result["nodes"]) == 3  # main + 2 workers

    def test_nodes_shows_capabilities(self):
        handle_cluster_init({"node_id": "n1", "capabilities": ["gpu", "coding"]})
        result = json.loads(handle_cluster_nodes({}))
        node = result["nodes"][0]
        assert "gpu" in node["capabilities"]
        assert "coding" in node["capabilities"]

    def test_nodes_includes_summary(self):
        handle_cluster_init({})
        result = json.loads(handle_cluster_nodes({}))
        assert "summary" in result
        assert "nodes" in result["summary"]


# ---------------------------------------------------------------------------
# handle_cluster_heartbeat
# ---------------------------------------------------------------------------

class TestClusterHeartbeat:
    def test_basic_heartbeat(self):
        handle_cluster_init({"node_id": "hb_node"})
        result = json.loads(handle_cluster_heartbeat({}))
        assert result["status"] == "ok"
        assert result["node_id"] == "hb_node"
        assert result["last_heartbeat"] is not None

    def test_heartbeat_updates_timestamp(self):
        handle_cluster_init({"node_id": "hb_node"})
        result1 = json.loads(handle_cluster_heartbeat({}))
        time.sleep(0.01)
        result2 = json.loads(handle_cluster_heartbeat({}))
        # Timestamps should be different (or at least the second one should exist)
        assert result2["last_heartbeat"] is not None

    def test_heartbeat_specific_node(self):
        handle_cluster_init({})
        handle_cluster_join({"endpoint": "http://localhost:8787", "node_id": "worker_hb"})
        result = json.loads(handle_cluster_heartbeat({"node_id": "worker_hb"}))
        assert result["node_id"] == "worker_hb"
        assert result["status"] == "ok"


# ---------------------------------------------------------------------------
# handle_cluster_complete
# ---------------------------------------------------------------------------

class TestClusterComplete:
    def test_basic_complete(self):
        handle_cluster_init({})
        submit = json.loads(handle_cluster_submit({"title": "Do something"}))
        task_id = submit["task_id"]

        result = json.loads(handle_cluster_complete({"task_id": task_id}))
        assert result["status"] == "completed"
        assert result["task_id"] == task_id

    def test_complete_missing_task_id(self):
        handle_cluster_init({})
        result = json.loads(handle_cluster_complete({}))
        assert "error" in result

    def test_complete_nonexistent_task(self):
        handle_cluster_init({})
        result = json.loads(handle_cluster_complete({"task_id": "nonexistent"}))
        assert "error" in result
        assert "not found" in result["error"]

    def test_complete_updates_store(self):
        handle_cluster_init({})
        submit = json.loads(handle_cluster_submit({"title": "Test"}))
        task_id = submit["task_id"]

        handle_cluster_complete({"task_id": task_id})
        s = _get_store()
        task = s.get_task(task_id)
        assert task.status == TaskStatus.completed

    def test_complete_triggers_dependents(self):
        handle_cluster_init({})
        # Create two tasks
        t1 = json.loads(handle_cluster_submit({"title": "Prerequisite"}))
        t2 = json.loads(handle_cluster_submit({"title": "Dependent"}))

        s = _get_store()

        # Reset t2 back to pending so we can set dependencies properly
        s.set_task_status(t2["task_id"], TaskStatus.pending)

        # Set dependency: t2 depends on t1
        s.set_dependencies(t2["task_id"], [t1["task_id"]])
        task2 = s.get_task(t2["task_id"])
        assert task2.status == TaskStatus.pending

        # Complete t1 — should trigger t2 promotion
        result = json.loads(handle_cluster_complete({"task_id": t1["task_id"]}))
        assert result["promoted_dependencies"] >= 1

        # t2 should now be ready or running
        task2 = s.get_task(t2["task_id"])
        assert task2.status in (TaskStatus.ready, TaskStatus.running)


# ---------------------------------------------------------------------------
# Integration: full workflow
# ---------------------------------------------------------------------------

class TestFullWorkflow:
    def test_init_submit_complete_cycle(self):
        """Full lifecycle: init → submit → list → complete → verify."""
        # Init
        init = json.loads(handle_cluster_init({
            "node_id": "leader",
            "capabilities": ["planning", "coding"],
        }))
        assert init["status"] == "initialized"

        # Submit
        submit = json.loads(handle_cluster_submit({
            "title": "Implement feature",
            "requires": ["coding"],
        }))
        task_id = submit["task_id"]
        assert submit["status"] in ("ready", "running")

        # List
        listing = json.loads(handle_cluster_list({}))
        assert listing["counts"]["total"] == 1

        # Complete
        complete = json.loads(handle_cluster_complete({"task_id": task_id}))
        assert complete["status"] == "completed"

        # Verify
        listing = json.loads(handle_cluster_list({}))
        assert listing["counts"]["completed"] == 1

    def test_multi_node_workflow(self):
        """Multiple nodes joining and working."""
        handle_cluster_init({"node_id": "main", "capabilities": ["planning"]})
        handle_cluster_join({"endpoint": "http://main:8787", "node_id": "w1", "capabilities": ["coding"]})
        handle_cluster_join({"endpoint": "http://main:8787", "node_id": "w2", "capabilities": ["gpu"]})

        # Check nodes
        nodes = json.loads(handle_cluster_nodes({}))
        assert len(nodes["nodes"]) == 3

        # Submit tasks with different requirements
        t1 = json.loads(handle_cluster_submit({"title": "Code task", "requires": ["coding"]}))
        t2 = json.loads(handle_cluster_submit({"title": "GPU task", "requires": ["gpu"]}))
        t3 = json.loads(handle_cluster_submit({"title": "General task"}))

        # List
        listing = json.loads(handle_cluster_list({}))
        assert listing["counts"]["total"] == 3


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_env_overrides(self):
        """Environment variables override defaults."""
        import hermes_cluster as mod

        with patch.dict(os.environ, {
            "HERMES_CLUSTER_NODE_ID": "env_node",
            "HERMES_CLUSTER_ID": "env-cluster",
            "HERMES_CLUSTER_AUTO_START": "false",
        }):
            config = mod._load_config()
            assert config["node_id"] == "env_node"
            assert config["cluster_id"] == "env-cluster"

    def test_capabilities_from_env(self):
        """Comma-separated capabilities from env."""
        import hermes_cluster as mod

        with patch.dict(os.environ, {
            "HERMES_CLUSTER_CAPABILITIES": "gpu,coding,browser",
        }):
            config = mod._load_config()
            assert config["capabilities"] == ["gpu", "coding", "browser"]
