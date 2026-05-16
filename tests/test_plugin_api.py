"""Tests for the rewritten plugin_api.py — direct FastAPI routes."""

from __future__ import annotations

import json
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Add the dashboard directory so plugin_api is importable
sys.path.insert(0, str(Path(__file__).parent.parent / "dashboard"))
# Add the plugin root so hermes_cluster is importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from hermes_cluster.models import (
    Node,
    NodeStatus,
    Task,
    TaskStatus,
    Lease,
    LeaseStatus,
    RecoveryEvent,
)
from hermes_cluster.state import ClusterState

# Import the plugin_api module
# We need to make it importable — copy it to a temp location or adjust sys.path
# For tests, we'll inline the router creation

from fastapi import APIRouter
from pydantic import BaseModel
from typing import Any, List, Optional
import yaml
import logging

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Inline the plugin_api router for testing (avoids import path issues)
# ---------------------------------------------------------------------------


def _create_test_router(state: ClusterState, config_paths=None):
    """Create a test instance of the plugin_api router."""
    from plugin_api import router as plugin_router, init as plugin_init

    # Patch the config paths for testing
    if config_paths:
        import plugin_api
        plugin_api._CONFIG_PATHS = config_paths

    plugin_init(state)
    return plugin_router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def state():
    """Create a fresh ClusterState for each test."""
    s = ClusterState()
    s.cluster_id = "test-cluster"
    s.node_id = "test-node"
    return s


@pytest.fixture
def app(state, tmp_path):
    """Create a FastAPI app with the plugin_api router."""
    # Create a temp config dir
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    config_file = config_dir / "cluster.yaml"
    config_file.write_text(yaml.dump({
        "cluster": {"id": "test-cluster"},
        "node": {"id": "test-node", "name": "test-node", "capabilities": ["coding"]},
        "server": {"port": 8787},
    }))

    # Import and configure
    from plugin_api import init as plugin_init, router
    plugin_init(state)
    # Override config paths
    import plugin_api
    plugin_api._CONFIG_PATHS = [config_file]

    app = FastAPI()
    app.include_router(router, prefix="/api/plugins/agent-cluster")
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


# ---------------------------------------------------------------------------
# Health endpoints
# ---------------------------------------------------------------------------


class TestHealth:
    def test_health_ok(self, client):
        resp = client.get("/api/plugins/agent-cluster/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["nodes"] == 0
        assert data["mode"] == "direct"

    def test_health_with_nodes(self, client, state):
        node = Node(id="n1", name="node-1", capabilities=["coding"])
        state.register_node(node)
        resp = client.get("/api/plugins/agent-cluster/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["nodes"] == 1


# ---------------------------------------------------------------------------
# Config endpoints
# ---------------------------------------------------------------------------


class TestConfig:
    def test_get_endpoint(self, client):
        resp = client.get("/api/plugins/agent-cluster/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["mode"] == "direct"

    def test_set_endpoint(self, client):
        resp = client.post("/api/plugins/agent-cluster/config",
                          json={"endpoint": "http://localhost:9999"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["mode"] == "direct"

    def test_get_node_config(self, client):
        resp = client.get("/api/plugins/agent-cluster/config/node")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["config_file"] is not None
        assert data["node"] is not None

    def test_get_config_yaml(self, client):
        resp = client.get("/api/plugins/agent-cluster/config/yaml")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "yaml" in data

    def test_save_config_yaml(self, client, tmp_path):
        new_yaml = yaml.dump({"cluster": {"id": "new-cluster"}})
        resp = client.put("/api/plugins/agent-cluster/config/yaml",
                         json={"yaml": new_yaml})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["needs_restart"] is True

    def test_save_invalid_yaml(self, client):
        resp = client.put("/api/plugins/agent-cluster/config/yaml",
                         json={"yaml": ": : : invalid"})
        assert resp.status_code == 400

    def test_restart_service_direct_mode(self, client):
        resp = client.post("/api/plugins/agent-cluster/config/restart")
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["mode"] == "direct"

    def test_update_capabilities(self, client, state, tmp_path):
        # Register a node first
        node = Node(id="test-node", name="test-node", capabilities=["old"])
        state.register_node(node)

        resp = client.put("/api/plugins/agent-cluster/config/capabilities",
                         json={"capabilities": ["coding", "reviewing"]})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["capabilities"] == ["coding", "reviewing"]

        # Verify runtime update
        updated_node = state.get_node("test-node")
        assert updated_node is not None
        assert "coding" in updated_node.capabilities
        assert "reviewing" in updated_node.capabilities


# ---------------------------------------------------------------------------
# Cluster data endpoints
# ---------------------------------------------------------------------------


class TestNodes:
    def test_list_nodes_empty(self, client):
        resp = client.get("/api/plugins/agent-cluster/nodes")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_nodes_with_data(self, client, state):
        node = Node(id="n1", name="node-1", capabilities=["coding"], load=0.5)
        state.register_node(node)
        resp = client.get("/api/plugins/agent-cluster/nodes")
        assert resp.status_code == 200
        nodes = resp.json()
        assert len(nodes) == 1
        assert nodes[0]["id"] == "n1"
        assert nodes[0]["name"] == "node-1"
        assert nodes[0]["capabilities"] == ["coding"]
        assert nodes[0]["load"] == 0.5


class TestTasks:
    def test_list_tasks_empty(self, client):
        resp = client.get("/api/plugins/agent-cluster/tasks")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_tasks_with_data(self, client, state):
        state.create_task("t1", "Test task", ["coding"], priority=1)
        resp = client.get("/api/plugins/agent-cluster/tasks")
        assert resp.status_code == 200
        tasks = resp.json()
        assert len(tasks) == 1
        assert tasks[0]["id"] == "t1"
        assert tasks[0]["title"] == "Test task"
        assert tasks[0]["priority"] == 1


class TestLeases:
    def test_list_leases_empty(self, client):
        resp = client.get("/api/plugins/agent-cluster/leases")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_leases_with_data(self, client, state):
        # Create a task and node first
        state.create_task("t1", "Task", ["coding"])
        node = Node(id="n1", name="node-1", capabilities=["coding"])
        state.register_node(node)
        state.create_lease("t1", "n1", timedelta(seconds=60))
        resp = client.get("/api/plugins/agent-cluster/leases")
        assert resp.status_code == 200
        leases = resp.json()
        assert len(leases) == 1
        assert leases[0]["task_id"] == "t1"
        assert leases[0]["node_id"] == "n1"


class TestStatus:
    def test_get_status_empty(self, client):
        resp = client.get("/api/plugins/agent-cluster/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert "summary" in data
        assert data["summary"]["total_tasks"] == 0
        assert data["summary"]["total_nodes"] == 0

    def test_get_status_with_data(self, client, state):
        node = Node(id="n1", name="node-1", capabilities=["coding"])
        state.register_node(node)
        state.create_task("t1", "Task 1", ["coding"], priority=1)
        state.create_task("t2", "Task 2", ["reviewing"], priority=2)

        resp = client.get("/api/plugins/agent-cluster/status")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 2
        assert data["summary"]["total_tasks"] == 2
        assert data["summary"]["total_nodes"] == 1

    def test_get_status_filter_by_node(self, client, state):
        node = Node(id="n1", name="node-1", capabilities=["coding"])
        state.register_node(node)
        state.create_task("t1", "Task 1", ["coding"])
        # Assign task to node
        state.set_task_status("t1", TaskStatus.running)
        state.get_task("t1").assigned_to = "n1"

        resp = client.get("/api/plugins/agent-cluster/status?node=n1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 1

    def test_get_status_filter_by_status(self, client, state):
        state.create_task("t1", "Task 1", [])
        state.create_task("t2", "Task 2", [])
        state.set_task_status("t1", TaskStatus.completed)

        resp = client.get("/api/plugins/agent-cluster/status?status=completed")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 1
        assert data["entries"][0]["task_id"] == "t1"


class TestTopology:
    def test_get_topology_empty(self, client):
        resp = client.get("/api/plugins/agent-cluster/topology")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"] == []

    def test_get_topology_with_nodes(self, client, state):
        node = Node(id="n1", name="node-1", capabilities=["coding"], load=0.3)
        state.register_node(node)
        resp = client.get("/api/plugins/agent-cluster/topology")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["nodes"]) == 1
        assert data["nodes"][0]["id"] == "n1"


class TestClusterMetrics:
    def test_get_metrics(self, client, state):
        node = Node(id="n1", name="node-1", capabilities=["coding"])
        state.register_node(node)
        state.create_task("t1", "Task", [])

        resp = client.get("/api/plugins/agent-cluster/cluster-metrics")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"]["total"] == 1
        assert data["nodes"]["online"] == 1
        assert data["tasks"]["total"] == 1


class TestTimeline:
    def test_get_timeline_empty(self, client):
        resp = client.get("/api/plugins/agent-cluster/timeline")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_timeline_with_events(self, client, state):
        event = RecoveryEvent(
            id="r1",
            task_id="t1",
            node_id="n1",
            action="reschedule",
            status="completed",
        )
        state.append_recovery_event(event)
        resp = client.get("/api/plugins/agent-cluster/timeline")
        assert resp.status_code == 200
        events = resp.json()
        assert len(events) == 1
        assert events[0]["action"] == "reschedule"


class TestWorkflowGraph:
    def test_get_graph_empty(self, client):
        resp = client.get("/api/plugins/agent-cluster/workflow/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert data["nodes"] == []
        assert data["edges"] == []

    def test_get_graph_with_dependencies(self, client, state):
        state.create_task("t1", "Task 1", [])
        state.create_task("t2", "Task 2", [])
        state.set_dependencies("t2", ["t1"])

        resp = client.get("/api/plugins/agent-cluster/workflow/graph")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1
        assert data["edges"][0]["from"] == "t1"
        assert data["edges"][0]["to"] == "t2"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_no_state_initialized(self, tmp_path):
        """Test that endpoints return 503 when state is not initialized."""
        from plugin_api import router
        import plugin_api

        # Save and clear state
        old_state = plugin_api._state
        plugin_api._state = None

        try:
            app = FastAPI()
            app.include_router(router, prefix="/api/plugins/agent-cluster")
            client = TestClient(app)

            resp = client.get("/api/plugins/agent-cluster/nodes")
            assert resp.status_code == 503
        finally:
            plugin_api._state = old_state



# ---------------------------------------------------------------------------
# Integration: full workflow
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_full_workflow(self, client, state):
        """Test a complete workflow: register node, submit task, schedule, complete."""
        # 1. Register a node
        node = Node(id="n1", name="main-node", capabilities=["coding", "reviewing"])
        state.register_node(node)

        # 2. Verify node is listed
        resp = client.get("/api/plugins/agent-cluster/nodes")
        assert len(resp.json()) == 1

        # 3. Check health
        resp = client.get("/api/plugins/agent-cluster/health")
        assert resp.json()["nodes"] == 1

        # 4. Submit a task (via state directly, since we don't have the task router)
        state.create_task("t1", "Implement feature", ["coding"], priority=1)
        state.trigger_pending_tasks()

        # 5. Verify task is in status
        resp = client.get("/api/plugins/agent-cluster/status")
        data = resp.json()
        assert data["summary"]["total_tasks"] == 1
        assert data["summary"]["ready"] == 1

        # 6. Schedule the task
        scheduled = state.schedule_pending()
        assert scheduled == 1

        # 7. Verify task is now running
        resp = client.get("/api/plugins/agent-cluster/tasks")
        tasks = resp.json()
        assert len(tasks) == 1
        assert tasks[0]["status"] == "running"
        assert tasks[0]["assigned_to"] == "n1"

        # 8. Check topology
        resp = client.get("/api/plugins/agent-cluster/topology")
        data = resp.json()
        assert len(data["nodes"]) == 1

        # 9. Check metrics
        resp = client.get("/api/plugins/agent-cluster/cluster-metrics")
        data = resp.json()
        assert data["tasks"]["running"] == 1

        # 10. Complete the task
        state.set_task_status("t1", TaskStatus.completed)

        # 11. Verify final state
        resp = client.get("/api/plugins/agent-cluster/status")
        data = resp.json()
        assert data["summary"]["completed"] == 1
        assert data["summary"]["running"] == 0
