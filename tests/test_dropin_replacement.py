"""Drop-in replacement test — verify ClusterStore works as a drop-in for ClusterState.

In v2, the architecture is simplified: plugin_api.py uses direct ClusterState
calls instead of a separate routers/ package. This test verifies that ClusterStore
has the same API surface as ClusterState.
"""

import sys
from pathlib import Path

# Add plugin root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from datetime import timedelta
from hermes_cluster.models import (
    Node, NodeStatus, Task, TaskStatus, Lease, LeaseStatus,
    RecoveryEvent, SchedulingDecision,
)
from hermes_cluster.state.cluster_store import ClusterStore


# ---------------------------------------------------------------------------
# Node API tests
# ---------------------------------------------------------------------------

class TestNodeAPI:
    """Verify ClusterStore node API matches ClusterState interface."""

    def _make_store(self):
        return ClusterStore(db_path=":memory:")

    def test_register_and_get(self):
        store = self._make_store()
        node = Node(id="n1", name="Node 1", capabilities=["coding"], status=NodeStatus.online)
        store.register_node(node)
        got = store.get_node("n1")
        assert got is not None
        assert got.id == "n1"
        assert got.name == "Node 1"
        assert got.capabilities == ["coding"]

    def test_get_all_nodes(self):
        store = self._make_store()
        store.register_node(Node(id="n1", name="N1"))
        store.register_node(Node(id="n2", name="N2"))
        nodes = store.get_all_nodes()
        assert len(nodes) == 2

    def test_update_heartbeat(self):
        store = self._make_store()
        store.register_node(Node(id="n1", name="N1"))
        store.update_heartbeat("n1")
        node = store.get_node("n1")
        assert node.status == NodeStatus.online

    def test_node_count(self):
        store = self._make_store()
        assert store.node_count() == 0
        store.register_node(Node(id="n1", name="N1"))
        assert store.node_count() == 1

    def test_online_count(self):
        store = self._make_store()
        store.register_node(Node(id="n1", name="N1", status=NodeStatus.online))
        store.register_node(Node(id="n2", name="N2", status=NodeStatus.offline))
        assert store.online_count() == 1


# ---------------------------------------------------------------------------
# Task API tests
# ---------------------------------------------------------------------------

class TestTaskAPI:
    """Verify ClusterStore task API matches ClusterState interface."""

    def _make_store(self):
        store = ClusterStore(db_path=":memory:")
        store.register_node(Node(id="n1", name="N1", capabilities=["coding"]))
        return store

    def test_create_and_get(self):
        store = self._make_store()
        task = store.create_task("t1", "Task 1", ["coding"], priority=1)
        assert task.id == "t1"
        got = store.get_task("t1")
        assert got is not None
        assert got.title == "Task 1"

    def test_set_task_status(self):
        store = self._make_store()
        store.create_task("t1", "Task 1", [])
        store.set_task_status("t1", TaskStatus.completed)
        task = store.get_task("t1")
        assert task.status == TaskStatus.completed

    def test_task_counts(self):
        store = self._make_store()
        store.create_task("t1", "T1", [])
        counts = store.task_counts()
        assert counts["total"] == 1
        assert counts["ready"] >= 1

    def test_trigger_pending(self):
        store = self._make_store()
        store.create_task("t1", "T1", [])
        result = store.trigger_pending_tasks()
        # trigger_pending_tasks returns a count (int) of promoted tasks
        assert isinstance(result, int)

    def test_schedule_pending(self):
        store = self._make_store()
        store.create_task("t1", "T1", ["coding"])
        scheduled = store.schedule_pending()
        assert scheduled >= 0

    def test_workflow_graph(self):
        store = self._make_store()
        store.create_task("t1", "T1", [])
        graph = store.get_workflow_graph()
        assert "nodes" in graph
        assert "edges" in graph
        assert len(graph["nodes"]) == 1


# ---------------------------------------------------------------------------
# Lease API tests
# ---------------------------------------------------------------------------

class TestLeaseAPI:
    """Verify ClusterStore lease API matches ClusterState interface."""

    def _make_store(self):
        store = ClusterStore(db_path=":memory:")
        store.register_node(Node(id="n1", name="N1"))
        return store

    def test_create_and_get_active(self):
        store = self._make_store()
        store.create_task("t1", "T1", [])
        lease = store.create_lease("t1", "n1", ttl=timedelta(seconds=60))
        assert lease is not None
        assert lease.task_id == "t1"
        active = store.get_active_leases()
        assert len(active) == 1

    def test_revoke_lease(self):
        store = self._make_store()
        store.create_task("t1", "T1", [])
        lease = store.create_lease("t1", "n1", ttl=timedelta(seconds=60))
        store.revoke_lease(lease.id)
        active = store.get_active_leases()
        assert len(active) == 0


# ---------------------------------------------------------------------------
# Summary / health API tests
# ---------------------------------------------------------------------------

class TestSummaryAPI:
    """Verify ClusterStore summary API matches ClusterState interface."""

    def _make_store(self):
        store = ClusterStore(db_path=":memory:")
        store.register_node(Node(id="n1", name="N1"))
        return store

    def test_summary(self):
        store = self._make_store()
        summary = store.get_summary()
        # Summary has nested structure: nodes.total, nodes.online
        assert "nodes" in summary
        assert summary["nodes"]["total"] == 1
        assert "uptime_seconds" in summary

    def test_sync_version(self):
        store = self._make_store()
        v = store.sync_version()
        assert isinstance(v, int)
