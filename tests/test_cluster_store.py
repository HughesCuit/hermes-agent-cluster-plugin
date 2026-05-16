"""Comprehensive tests for ClusterStore — SQLite-backed persistent storage.

Tests mirror the API surface of ClusterState to verify drop-in compatibility.
"""

import json
import os
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

# Adjust import path for this workspace
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from hermes_cluster.state.cluster_store import ClusterStore
from hermes_cluster.models import (
    BatchSyncMessage,
    Delivery,
    EventType,
    FederationClusterStatus,
    Hook,
    Lease,
    LeaseStatus,
    Node,
    NodeStatus,
    RecoveryEvent,
    RemoteCluster,
    SchedulingDecision,
    SchedulingStats,
    SyncEventType,
    SyncMessage,
    Task,
    TaskStatus,
    TaskSync,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store():
    """Create a fresh in-memory SQLite store."""
    s = ClusterStore(db_path=":memory:")
    s.cluster_id = "test-cluster"
    s.node_id = "test-node"
    s.node_role = "main"
    yield s
    s.close()


@pytest.fixture
def file_store(tmp_path):
    """Create a file-backed SQLite store for persistence tests."""
    db_path = str(tmp_path / "test.db")
    s = ClusterStore(db_path=db_path)
    s.cluster_id = "test-cluster"
    s.node_id = "test-node"
    yield s
    s.close()


# ---------------------------------------------------------------------------
# Node registry tests
# ---------------------------------------------------------------------------

class TestNodeRegistry:
    def test_register_and_get(self, store):
        node = Node(id="node_1", name="worker-1", capabilities=["coding", "gpu"])
        store.register_node(node)
        got = store.get_node("node_1")
        assert got is not None
        assert got.id == "node_1"
        assert got.name == "worker-1"
        assert got.capabilities == ["coding", "gpu"]
        assert got.status == NodeStatus.online

    def test_get_nonexistent(self, store):
        assert store.get_node("nope") is None

    def test_get_all_nodes(self, store):
        store.register_node(Node(id="n1", name="one"))
        store.register_node(Node(id="n2", name="two"))
        all_nodes = store.get_all_nodes()
        assert len(all_nodes) == 2
        ids = {n.id for n in all_nodes}
        assert ids == {"n1", "n2"}

    def test_node_count(self, store):
        assert store.node_count() == 0
        store.register_node(Node(id="n1", name="one"))
        assert store.node_count() == 1

    def test_online_count(self, store):
        store.register_node(Node(id="n1", name="one", status=NodeStatus.online))
        store.register_node(Node(id="n2", name="two", status=NodeStatus.offline))
        assert store.online_count() == 1

    def test_update_heartbeat(self, store):
        store.register_node(Node(id="n1", name="one", status=NodeStatus.offline))
        store.update_heartbeat("n1")
        node = store.get_node("n1")
        assert node.status == NodeStatus.online

    def test_update_capabilities(self, store):
        store.register_node(Node(id="n1", name="one", capabilities=["old"]))
        store.update_capabilities("n1", ["new", "caps"])
        node = store.get_node("n1")
        assert node.capabilities == ["new", "caps"]

    def test_on_node_online_callback(self, store):
        called = []
        store.set_on_node_online(lambda nid: called.append(nid))
        store.register_node(Node(id="n1", name="one"))
        assert called == ["n1"]

    def test_on_capability_change_callback(self, store):
        changes = []
        store.set_on_capability_change(lambda nid, old, new: changes.append((nid, old, new)))
        store.register_node(Node(id="n1", name="one", capabilities=["a"]))
        store.update_capabilities("n1", ["b", "c"])
        assert changes == [("n1", ["a"], ["b", "c"])]

    def test_upsert_node(self, store):
        """Registering the same ID updates the node."""
        store.register_node(Node(id="n1", name="old", capabilities=["a"]))
        store.register_node(Node(id="n1", name="new", capabilities=["b"]))
        node = store.get_node("n1")
        assert node.name == "new"
        assert node.capabilities == ["b"]


# ---------------------------------------------------------------------------
# Task store tests
# ---------------------------------------------------------------------------

class TestTaskStore:
    def test_create_and_get(self, store):
        task = store.create_task("t1", "Test task", ["coding"], priority=2)
        assert task.id == "t1"
        assert task.title == "Test task"
        assert task.priority == 2
        # Auto-promoted: create_task promotes no-dep tasks to ready for API compat
        assert task.status == TaskStatus.ready

        got = store.get_task("t1")
        assert got is not None
        assert got.title == "Test task"

    def test_get_nonexistent(self, store):
        assert store.get_task("nope") is None

    def test_get_all_tasks(self, store):
        store.create_task("t1", "Task 1", [])
        store.create_task("t2", "Task 2", [])
        all_tasks = store.get_all_tasks()
        assert len(all_tasks) == 2

    def test_set_task_status(self, store):
        store.create_task("t1", "Task 1", [])
        result = store.set_task_status("t1", TaskStatus.running)
        assert result is True
        task = store.get_task("t1")
        assert task.status == TaskStatus.running
        assert task.version == 2  # incremented

    def test_set_task_status_nonexistent(self, store):
        result = store.set_task_status("nope", TaskStatus.running)
        assert result is False

    def test_set_task_status_with_fail_reason(self, store):
        store.create_task("t1", "Task 1", [])
        store.set_task_status("t1", TaskStatus.failed, fail_reason="OOM")
        task = store.get_task("t1")
        assert task.status == TaskStatus.failed
        assert task.fail_reason == "OOM"

    def test_unblock_task(self, store):
        store.create_task("t1", "Task 1", [])
        store.set_task_status("t1", TaskStatus.blocked)
        result = store.unblock_task("t1")
        assert result is True
        task = store.get_task("t1")
        assert task.status == TaskStatus.pending

    def test_unblock_non_blocked(self, store):
        store.create_task("t1", "Task 1", [])
        result = store.unblock_task("t1")
        assert result is False

    def test_set_dependencies(self, store):
        store.create_task("t1", "Parent", [])
        store.create_task("t2", "Child", [])
        result = store.set_dependencies("t2", ["t1"])
        assert result is True
        task = store.get_task("t2")
        assert task.depends_on == ["t1"]

    def test_get_dependents(self, store):
        store.create_task("t1", "Parent", [])
        store.create_task("t2", "Child", [])
        store.set_dependencies("t2", ["t1"])
        dependents = store.get_dependents("t1")
        assert "t2" in dependents

    def test_get_trigger_chain(self, store):
        store.create_task("t1", "Root", [])
        store.create_task("t2", "Child", [])
        store.create_task("t3", "Grandchild", [])
        store.set_dependencies("t2", ["t1"])
        store.set_dependencies("t3", ["t2"])
        chain = store.get_trigger_chain("t1")
        assert "t2" in chain
        assert "t3" in chain

    def test_get_workflow_graph(self, store):
        store.create_task("t1", "A", [])
        store.create_task("t2", "B", [])
        store.set_dependencies("t2", ["t1"])
        graph = store.get_workflow_graph()
        assert len(graph["nodes"]) == 2
        assert len(graph["edges"]) == 1
        assert graph["edges"][0] == {"from": "t1", "to": "t2"}

    def test_task_counts(self, store):
        store.create_task("t1", "A", [])
        store.create_task("t2", "B", [])
        store.create_task("t3", "C", [])
        store.set_task_status("t1", TaskStatus.ready)
        store.set_task_status("t2", TaskStatus.running)
        store.set_task_status("t3", TaskStatus.completed)
        counts = store.task_counts()
        assert counts["total"] == 3
        assert counts["ready"] == 1
        assert counts["running"] == 1
        assert counts["completed"] == 1

    def test_version_increments(self, store):
        store.create_task("t1", "A", [])
        t = store.get_task("t1")
        assert t.version == 1
        store.set_task_status("t1", TaskStatus.ready)
        t = store.get_task("t1")
        assert t.version == 2


# ---------------------------------------------------------------------------
# Lease manager tests
# ---------------------------------------------------------------------------

class TestLeaseManager:
    def test_create_lease(self, store):
        store.create_task("t1", "Task", [])
        lease = store.create_lease("t1", "node_1", timedelta(seconds=60))
        assert lease.id.startswith("lease_")
        assert lease.task_id == "t1"
        assert lease.node_id == "node_1"
        assert lease.status == LeaseStatus.active

    def test_revoke_lease(self, store):
        store.create_task("t1", "Task", [])
        lease = store.create_lease("t1", "node_1", timedelta(seconds=60))
        result = store.revoke_lease(lease.id)
        assert result is True
        # Should not appear in active leases
        active = store.get_active_leases()
        assert all(l.id != lease.id for l in active)

    def test_revoke_nonexistent(self, store):
        result = store.revoke_lease("nope")
        assert result is False

    def test_active_leases(self, store):
        store.create_task("t1", "Task", [])
        lease = store.create_lease("t1", "node_1", timedelta(seconds=60))
        active = store.get_active_leases()
        assert len(active) >= 1
        assert any(l.id == lease.id for l in active)

    def test_expired_lease(self, store):
        store.create_task("t1", "Task", [])
        lease = store.create_lease("t1", "node_1", timedelta(seconds=-1))  # already expired
        active = store.get_active_leases()
        assert all(l.id != lease.id for l in active)

    def test_lease_expiry_callback(self, store):
        store.create_task("t1", "Task", [])
        expired = []
        store.set_lease_callback(lambda tid, nid: expired.append((tid, nid)))
        store.create_lease("t1", "node_1", timedelta(seconds=-1))
        store.get_active_leases()  # triggers expiry
        assert ("t1", "node_1") in expired


# ---------------------------------------------------------------------------
# Sync tests
# ---------------------------------------------------------------------------

class TestSync:
    def test_sync_message(self, store):
        msg = SyncMessage(version=1, sender_node="node_1", event_type=SyncEventType.task_created)
        result = store.handle_sync_message(msg)
        assert result is True
        assert store.sync_version() == 1

    def test_sync_message_out_of_order(self, store):
        msg1 = SyncMessage(version=2, sender_node="node_1")
        store.handle_sync_message(msg1)
        msg_old = SyncMessage(version=1, sender_node="node_1")
        result = store.handle_sync_message(msg_old)
        assert result is False
        assert store.sync_version() == 2

    def test_sync_with_task_state(self, store):
        task_sync = TaskSync(task_id="t1", title="Synced task", status="running", version=5)
        msg = SyncMessage(version=1, sender_node="node_1", task_state=task_sync)
        store.handle_sync_message(msg)
        task = store.get_task("t1")
        assert task is not None
        assert task.title == "Synced task"
        assert task.status == TaskStatus.running

    def test_sync_updates_existing_task(self, store):
        store.create_task("t1", "Original", [])
        task_sync = TaskSync(task_id="t1", title="Updated", status="completed", version=10)
        msg = SyncMessage(version=1, sender_node="node_1", task_state=task_sync)
        store.handle_sync_message(msg)
        task = store.get_task("t1")
        assert task.status == TaskStatus.completed

    def test_batch_sync(self, store):
        batch = BatchSyncMessage(messages=[
            SyncMessage(version=1, sender_node="n1", event_type=SyncEventType.task_created),
            SyncMessage(version=2, sender_node="n1", event_type=SyncEventType.task_completed),
            SyncMessage(version=3, sender_node="n1", event_type=SyncEventType.task_failed),
        ])
        count = store.handle_batch_sync(batch)
        assert count == 3
        assert store.sync_version() == 3


# ---------------------------------------------------------------------------
# Recovery tests
# ---------------------------------------------------------------------------

class TestRecovery:
    def test_append_event(self, store):
        event = RecoveryEvent(
            id="r1", node_id="n1", action="reschedule",
            status="completed", message="Test",
        )
        store.append_recovery_event(event)
        events = store.get_recovery_events()
        assert len(events) == 1
        assert events[0].id == "r1"

    def test_trigger_recovery(self, store):
        store.trigger_recovery("offline-node")
        events = store.get_recovery_events()
        assert len(events) == 1
        assert events[0].node_id == "offline-node"
        assert events[0].action == "reschedule"

    def test_recovery_stats(self, store):
        store.trigger_recovery("n1")
        store.trigger_recovery("n2")
        stats = store.recovery_stats()
        assert stats["total"] == 2
        assert stats["by_action"]["reschedule"] == 2


# ---------------------------------------------------------------------------
# Scheduling tests
# ---------------------------------------------------------------------------

class TestScheduling:
    def test_record_decision(self, store):
        d = SchedulingDecision(
            task_id="t1", task_title="Task", priority=1,
            node_id="n1", score=0.9, reason="capability_match",
        )
        store.record_decision(d)
        decisions = store.get_decisions()
        assert len(decisions) == 1
        assert decisions[0].task_id == "t1"

    def test_schedule_stats(self, store):
        store.record_decision(SchedulingDecision(
            task_id="t1", task_title="A", priority=1, node_id="n1", score=1.0, reason="test",
        ))
        store.record_decision(SchedulingDecision(
            task_id="t2", task_title="B", priority=3, node_id="n1", score=0.8, reason="test",
        ))
        stats = store.get_schedule_stats()
        assert stats.total_decisions == 2
        assert stats.decisions_by_priority[1] == 1
        assert stats.decisions_by_priority[3] == 1

    def test_max_decisions_trimmed(self, store):
        store._max_decisions = 5
        for i in range(10):
            store.record_decision(SchedulingDecision(
                task_id=f"t{i}", task_title=f"Task {i}",
                priority=1, node_id="n1", score=1.0, reason="test",
            ))
        decisions = store.get_decisions()
        assert len(decisions) == 5

    def test_trigger_pending_tasks(self, store):
        # t1 is auto-promoted to ready on create (no deps)
        store.create_task("t1", "No deps", [])
        store.create_task("t2", "Has unmet deps", [])
        store.set_dependencies("t2", ["t1"])
        # t1 already ready from create; t2 has unmet deps (t1 ready, not completed)
        promoted = store.trigger_pending_tasks()
        assert promoted == 0  # nothing to promote
        assert store.get_task("t1").status == TaskStatus.ready
        assert store.get_task("t2").status == TaskStatus.pending

    def test_trigger_pending_all_deps_met(self, store):
        store.create_task("t1", "Parent", [])
        store.create_task("t2", "Child", [])
        store.set_dependencies("t2", ["t1"])
        # Both auto-promoted to ready on create; demote t2 to pending to simulate
        store.set_task_status("t2", TaskStatus.pending)
        store.set_task_status("t1", TaskStatus.completed)
        promoted = store.trigger_pending_tasks()
        # t1 already completed, t2 now has all deps met
        assert promoted == 1
        assert store.get_task("t1").status == TaskStatus.completed
        assert store.get_task("t2").status == TaskStatus.ready

    def test_schedule_pending(self, store):
        store.register_node(Node(id="n1", name="worker", capabilities=["coding"]))
        store.create_task("t1", "Task", ["coding"])
        store.trigger_pending_tasks()  # promote to ready
        scheduled = store.schedule_pending()
        assert scheduled == 1
        task = store.get_task("t1")
        assert task.status == TaskStatus.running
        assert task.assigned_to == "n1"

    def test_schedule_pending_no_matching_nodes(self, store):
        store.register_node(Node(id="n1", name="worker", capabilities=["gpu"]))
        store.create_task("t1", "Task", ["coding"])
        store.trigger_pending_tasks()
        scheduled = store.schedule_pending()
        assert scheduled == 0
        assert store.get_task("t1").status == TaskStatus.ready


# ---------------------------------------------------------------------------
# Federation tests
# ---------------------------------------------------------------------------

class TestFederation:
    def test_register_cluster(self, store):
        cluster = store.register_federation_cluster("c1", "remote", "http://remote:8787")
        assert cluster.id == "c1"
        assert cluster.name == "remote"
        assert cluster.status == FederationClusterStatus.available

    def test_get_federation_cluster(self, store):
        store.register_federation_cluster("c1", "remote", "http://remote:8787")
        got = store.get_federation_cluster("c1")
        assert got is not None
        assert got.name == "remote"

    def test_get_all_federation_clusters(self, store):
        store.register_federation_cluster("c1", "one", "http://one:8787")
        store.register_federation_cluster("c2", "two", "http://two:8787")
        clusters = store.get_federation_clusters()
        assert len(clusters) == 2

    def test_remove_federation_cluster(self, store):
        store.register_federation_cluster("c1", "remote", "http://remote:8787")
        result = store.remove_federation_cluster("c1")
        assert result is True
        assert store.get_federation_cluster("c1") is None

    def test_remove_nonexistent(self, store):
        result = store.remove_federation_cluster("nope")
        assert result is False


# ---------------------------------------------------------------------------
# Hook tests
# ---------------------------------------------------------------------------

class TestHooks:
    def test_register_hook(self, store):
        hook = store.register_hook(
            "http://example.com/webhook",
            [EventType.task_created, EventType.task_completed],
            secret="s3cret",
        )
        assert hook.id.startswith("hook_")
        assert hook.url == "http://example.com/webhook"
        assert len(hook.events) == 2

    def test_list_hooks_hides_secret(self, store):
        store.register_hook("http://example.com", [EventType.task_created], secret="secret")
        hooks = store.list_hooks()
        assert len(hooks) == 1
        assert hooks[0].secret is None  # hidden

    def test_deregister_hook(self, store):
        hook = store.register_hook("http://example.com", [EventType.task_created])
        result = store.deregister_hook(hook.id)
        assert result is True
        assert len(store.list_hooks()) == 0

    def test_deregister_nonexistent(self, store):
        result = store.deregister_hook("nope")
        assert result is False

    def test_add_delivery(self, store):
        hook = store.register_hook("http://example.com", [EventType.task_created])
        delivery = Delivery(
            id="d1", hook_id=hook.id, event_type="task_created",
            payload={"task_id": "t1"}, status="delivered",
        )
        store.add_delivery(delivery)
        deliveries = store.get_hook_deliveries(hook.id)
        assert len(deliveries) == 1
        assert deliveries[0].payload == {"task_id": "t1"}

    def test_max_deliveries_trimmed(self, store):
        store._max_deliveries = 5
        hook = store.register_hook("http://example.com", [EventType.task_created])
        for i in range(10):
            store.add_delivery(Delivery(
                id=f"d{i}", hook_id=hook.id, event_type="task_created",
                payload={}, status="delivered",
            ))
        deliveries = store.get_hook_deliveries(hook.id)
        assert len(deliveries) == 5


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------

class TestConfig:
    def test_get_set_config(self, store):
        assert store.get_config() is None
        config = {"cluster": {"id": "c1"}, "node": {"id": "n1"}}
        store.set_config(config)
        got = store.get_config()
        assert got == config

    def test_config_path(self, store):
        assert store.get_config_path() == ""
        store.set_config_path("/tmp/config.yaml")
        assert store.get_config_path() == "/tmp/config.yaml"

    def test_config_persists_in_kv(self, store):
        config = {"key": "value"}
        store.set_config(config)
        # Get from kv_store directly
        with store._lock:
            row = store._conn.execute(
                "SELECT value FROM kv_store WHERE key = 'cluster_config'"
            ).fetchone()
        assert row is not None
        assert json.loads(row["value"]) == config


# ---------------------------------------------------------------------------
# Summary tests
# ---------------------------------------------------------------------------

class TestSummary:
    def test_summary(self, store):
        summary = store.get_summary()
        assert summary["cluster_id"] == "test-cluster"
        assert summary["node_id"] == "test-node"
        assert summary["role"] == "main"
        assert "nodes" in summary
        assert "tasks" in summary
        assert "leases" in summary
        assert "sync_version" in summary
        assert "uptime_seconds" in summary


# ---------------------------------------------------------------------------
# Persistence tests
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_data_persists_across_connections(self, file_store):
        """Data written to a file-backed store survives reconnection."""
        file_store.create_task("t1", "Persistent task", [])
        file_store.register_node(Node(id="n1", name="worker"))
        file_store.set_task_status("t1", TaskStatus.running)

        # Close and reopen
        file_store.close()
        new_store = ClusterStore(db_path=file_store._db_path)
        new_store.cluster_id = "test-cluster"

        task = new_store.get_task("t1")
        assert task is not None
        assert task.status == TaskStatus.running

        node = new_store.get_node("n1")
        assert node is not None
        assert node.name == "worker"

        new_store.close()

    def test_config_persists(self, file_store):
        file_store.set_config({"key": "value"})
        file_store.close()

        new_store = ClusterStore(db_path=file_store._db_path)
        assert new_store.get_config() == {"key": "value"}
        new_store.close()


# ---------------------------------------------------------------------------
# Thread safety tests
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_node_registers(self, store):
        """Multiple threads can register nodes without crashing."""
        errors = []

        def register_node(i):
            try:
                store.register_node(Node(id=f"n{i}", name=f"node-{i}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=register_node, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert store.node_count() == 20

    def test_concurrent_task_operations(self, store):
        """Multiple threads can create and update tasks."""
        errors = []

        def create_and_update(i):
            try:
                store.create_task(f"t{i}", f"Task {i}", [])
                store.set_task_status(f"t{i}", TaskStatus.running)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_and_update, args=(i,)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        assert store.task_counts()["total"] == 20
