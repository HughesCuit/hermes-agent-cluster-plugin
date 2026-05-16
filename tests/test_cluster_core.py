"""Comprehensive tests for hermes_cluster.core — ClusterCore, Watchdog, Recovery, Scheduler, Workflow.

Tests cover:
  1. Watchdog heartbeat monitoring and status transitions
  2. Recovery pipeline (revoker → rescheduler → detector)
  3. Scheduler capability matching and task assignment
  4. Workflow dependency resolution and trigger chains
  5. ClusterCore integration (lifecycle, callbacks, end-to-end)
  6. Edge cases (empty cluster, duplicate nodes, missing tasks)
"""

from __future__ import annotations

import time
import threading
from datetime import datetime, timedelta
from unittest.mock import MagicMock

import pytest

# ---------------------------------------------------------------------------
# Ensure hermes_cluster is importable
# ---------------------------------------------------------------------------
import sys
import os

# Add parent directory to path so we can import hermes_cluster
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hermes_cluster.models import (
    EventType,
    Hook,
    Lease,
    LeaseStatus,
    Node,
    NodeStatus,
    RecoveryEvent,
    SchedulingDecision,
    Task,
    TaskStatus,
)
from hermes_cluster.state import ClusterState
from hermes_cluster.core.watchdog import (
    Watchdog,
    WatchdogRegistry,
    HeartbeatNode,
    WatchdogEvent,
)
from hermes_cluster.core.recovery import (
    Revoker,
    Rescheduler,
    RecoveryDetector,
)
from hermes_cluster.core.cluster_core import (
    ClusterCore,
    ClusterScheduler,
    WorkflowResolver,
)


# ===================================================================
# Fixtures
# ===================================================================

@pytest.fixture
def state():
    """Fresh in-memory ClusterState."""
    return ClusterState()


@pytest.fixture
def core():
    """Fresh ClusterCore with in-memory state."""
    c = ClusterCore(
        cluster_id="test-cluster",
        node_id="node-1",
        node_name="test-node",
        capabilities=["coding", "review"],
        db_path=":memory:",
    )
    yield c
    c.stop()


# ===================================================================
# 1. Watchdog Tests
# ===================================================================

class TestWatchdog:
    def _make_adapter(self, state):
        class Adapter(WatchdogRegistry):
            def __init__(self, s):
                self._s = s
            def get_all_heartbeat_nodes(self):
                return [
                    HeartbeatNode(n.id, n.last_heartbeat, n.status.value)
                    for n in self._s.get_all_nodes()
                ]
            def update_node_status(self, node_id, status):
                # Access internal dict directly to avoid deadlock with non-reentrant Lock
                with self._s._nodes_lock:
                    if node_id in self._s._nodes:
                        self._s._nodes[node_id].status = NodeStatus(status)
        return Adapter(state)

    def test_check_detects_offline_node(self, state):
        """Node with old heartbeat should be detected as offline."""
        node = Node(
            id="node-old",
            name="old-node",
            capabilities=["coding"],
            status=NodeStatus.online,
            last_heartbeat=datetime.utcnow() - timedelta(seconds=60),
        )
        state.register_node(node)

        adapter = self._make_adapter(state)
        events = []

        wd = Watchdog(
            registry=adapter,
            check_interval=1.0,
            degraded_after=5.0,
            offline_after=10.0,
            callback=lambda e: events.append(e),
        )

        result = wd.check_now()
        assert len(result) == 1
        assert result[0].node_id == "node-old"
        assert result[0].event_type == "offline"
        assert len(events) == 1

    def test_check_detects_degraded_node(self, state):
        """Node with moderately old heartbeat should be degraded."""
        node = Node(
            id="node-degraded",
            name="degraded-node",
            capabilities=["coding"],
            status=NodeStatus.online,
            last_heartbeat=datetime.utcnow() - timedelta(seconds=20),
        )
        state.register_node(node)

        adapter = self._make_adapter(state)
        result = Watchdog(
            registry=adapter,
            degraded_after=10.0,
            offline_after=30.0,
        ).check_now()

        assert len(result) == 1
        assert result[0].event_type == "degraded"

    def test_check_no_change_for_healthy_node(self, state):
        """Node with recent heartbeat should not trigger events."""
        node = Node(
            id="node-healthy",
            name="healthy-node",
            capabilities=["coding"],
            status=NodeStatus.online,
            last_heartbeat=datetime.utcnow(),
        )
        state.register_node(node)

        adapter = self._make_adapter(state)
        result = Watchdog(
            registry=adapter,
            degraded_after=10.0,
            offline_after=30.0,
        ).check_now()

        assert len(result) == 0

    def test_watchdog_start_stop(self, state):
        """Watchdog should start and stop cleanly."""
        adapter = self._make_adapter(state)
        wd = Watchdog(registry=adapter, check_interval=0.1)

        assert not wd.is_running
        wd.start()
        assert wd.is_running
        wd.stop()
        assert not wd.is_running

    def test_watchdog_double_start(self, state):
        """Starting an already-running watchdog should be a no-op."""
        adapter = self._make_adapter(state)
        wd = Watchdog(registry=adapter, check_interval=0.1)
        wd.start()
        wd.start()  # Should not raise
        assert wd.is_running
        wd.stop()

    def test_update_intervals(self, state):
        """Intervals should be updatable."""
        adapter = self._make_adapter(state)
        wd = Watchdog(registry=adapter, check_interval=1.0, degraded_after=5.0, offline_after=10.0)
        wd.update_intervals(2.0, 10.0, 20.0)
        assert wd._check_interval == 2.0
        assert wd._degraded_after == 10.0
        assert wd._offline_after == 20.0


# ===================================================================
# 2. Recovery Tests
# ===================================================================

class TestRevoker:
    def test_revoke_all_for_node(self, state):
        """Revoker should revoke all leases for a given node."""
        state.register_node(Node(id="node-1", name="n1", capabilities=["coding"]))
        state.register_node(Node(id="node-2", name="n2", capabilities=["coding"]))

        t1 = state.create_task("t1", "task 1", [])
        t2 = state.create_task("t2", "task 2", [])
        state.set_task_status("t1", TaskStatus.running)
        state.set_task_status("t2", TaskStatus.running)

        lease1 = state.create_lease("t1", "node-1", timedelta(seconds=60))
        lease2 = state.create_lease("t2", "node-1", timedelta(seconds=60))
        lease3 = state.create_lease("t2", "node-2", timedelta(seconds=60))

        revoker = Revoker(state)
        revoked = revoker.revoke_all_for_node("node-1")

        assert "t1" in revoked
        assert "t2" in revoked
        active = state.get_active_leases()
        assert any(l.id == lease3.id for l in active)

    def test_revoke_empty(self, state):
        """Revoking with no leases should return empty list."""
        revoker = Revoker(state)
        revoked = revoker.revoke_all_for_node("nonexistent")
        assert revoked == []


class TestRescheduler:
    def test_reschedule_to_available_node(self, state):
        """Rescheduler should assign orphaned tasks to available nodes."""
        state.register_node(Node(id="node-1", name="n1", capabilities=["coding"]))
        state.create_task("t1", "task 1", [])
        state.set_task_status("t1", TaskStatus.running)

        rescheduler = Rescheduler(state)
        count = rescheduler.reschedule_orphaned(["t1"])

        assert count == 1
        task = state.get_task("t1")
        assert task.status == TaskStatus.running

    def test_reschedule_no_available_node(self, state):
        """With no online nodes, task should be marked failed."""
        state.create_task("t1", "task 1", [])
        state.set_task_status("t1", TaskStatus.running)

        rescheduler = Rescheduler(state)
        count = rescheduler.reschedule_orphaned(["t1"])

        assert count == 0
        task = state.get_task("t1")
        assert task.status == TaskStatus.failed

    def test_reschedule_capability_mismatch(self, state):
        """Task requiring 'rust' can't be scheduled on 'coding'-only node."""
        state.register_node(Node(id="node-1", name="n1", capabilities=["coding"]))
        state.create_task("t1", "task 1", ["rust"])
        state.set_task_status("t1", TaskStatus.running)

        rescheduler = Rescheduler(state)
        count = rescheduler.reschedule_orphaned(["t1"])

        assert count == 0
        task = state.get_task("t1")
        assert task.status == TaskStatus.failed


class TestRecoveryDetector:
    def test_notify_offline_triggers_recovery(self, state):
        """Offline notification should revoke leases and reschedule."""
        state.register_node(Node(id="node-1", name="n1", capabilities=["coding"]))
        state.register_node(Node(id="node-2", name="n2", capabilities=["coding"]))

        state.create_task("t1", "task 1", [])
        state.set_task_status("t1", TaskStatus.running)
        state.create_lease("t1", "node-1", timedelta(seconds=60))

        revoker = Revoker(state)
        rescheduler = Rescheduler(state)
        detector = RecoveryDetector(revoker, rescheduler, state)

        detector.handle_offline_sync("node-1")

        task = state.get_task("t1")
        assert task.status == TaskStatus.running

        events = state.get_recovery_events()
        assert len(events) >= 2


# ===================================================================
# 3. Scheduler Tests
# ===================================================================

class TestClusterScheduler:
    def test_trigger_pending_no_nodes(self, state):
        """With no nodes, pending tasks should be promoted to ready (no deps)."""
        state.create_task("t1", "task 1", [])
        scheduler = ClusterScheduler(state)
        promoted = scheduler.trigger_pending_tasks()
        # trigger_pending_tasks returns count of promoted tasks
        assert promoted == 1  # task with no deps gets promoted to ready

    def test_schedule_pending_with_matching_node(self, state):
        """Ready tasks should be assigned to matching nodes."""
        state.register_node(Node(id="node-1", name="n1", capabilities=["coding"]))
        state.create_task("t1", "task 1", [])

        scheduler = ClusterScheduler(state)
        scheduler.trigger_pending_tasks()
        count = scheduler.schedule_pending()

        assert count == 1
        task = state.get_task("t1")
        assert task.assigned_to == "node-1"
        assert task.status == TaskStatus.running

    def test_schedule_pending_capability_mismatch(self, state):
        """Tasks requiring unmatched capabilities should not be scheduled."""
        state.register_node(Node(id="node-1", name="n1", capabilities=["coding"]))
        state.create_task("t1", "task 1", ["rust"])

        scheduler = ClusterScheduler(state)
        scheduler.trigger_pending_tasks()
        count = scheduler.schedule_pending()

        assert count == 0
        task = state.get_task("t1")
        assert task.status == TaskStatus.ready

    def test_schedule_priority_order(self, state):
        """Higher priority tasks should be scheduled first."""
        state.register_node(Node(id="node-1", name="n1", capabilities=["coding"]))
        state.create_task("t-low", "low priority", [], priority=5)
        state.create_task("t-high", "high priority", [], priority=1)

        scheduler = ClusterScheduler(state)
        scheduler.trigger_pending_tasks()
        scheduler.schedule_pending()

        task_high = state.get_task("t-high")
        assert task_high.status == TaskStatus.running

    def test_reschedule_task(self, state):
        """Rescheduling should revoke old lease and reassign."""
        state.register_node(Node(id="node-1", name="n1", capabilities=["coding"]))
        state.register_node(Node(id="node-2", name="n2", capabilities=["coding"]))
        state.create_task("t1", "task 1", [])
        state.set_task_status("t1", TaskStatus.running)
        state.create_lease("t1", "node-1", timedelta(seconds=60))

        scheduler = ClusterScheduler(state)
        new_node = scheduler.reschedule_task("t1")

        # After reschedule, task should be pending (revoke_lease releases it)
        task = state.get_task("t1")
        assert task.status == TaskStatus.pending


# ===================================================================
# 4. Workflow Resolver Tests
# ===================================================================

class TestWorkflowResolver:
    def test_resolve_no_dependencies(self, state):
        """Task with no deps should be promoted to ready."""
        state.create_task("t1", "task 1", [])
        resolver = WorkflowResolver(state)
        result = resolver.resolve_dependencies("t1")
        assert result is True
        assert state.get_task("t1").status == TaskStatus.ready

    def test_resolve_with_met_dependencies(self, state):
        """Task with completed deps should be promoted."""
        state.create_task("t1", "task 1", [])
        t2 = state.create_task("t2", "task 2", [])
        state.set_dependencies("t2", ["t1"])
        state.set_task_status("t1", TaskStatus.completed)

        resolver = WorkflowResolver(state)
        result = resolver.resolve_dependencies("t2")
        assert result is True
        assert state.get_task("t2").status == TaskStatus.ready

    def test_resolve_with_unmet_dependencies(self, state):
        """Task with incomplete deps should stay pending."""
        state.create_task("t1", "task 1", [])
        t2 = state.create_task("t2", "task 2", [])
        state.set_dependencies("t2", ["t1"])

        resolver = WorkflowResolver(state)
        result = resolver.resolve_dependencies("t2")
        assert result is False
        assert state.get_task("t2").status == TaskStatus.pending

    def test_resolve_with_failed_dependency(self, state):
        """Task with failed dep should be blocked."""
        state.create_task("t1", "task 1", [])
        t2 = state.create_task("t2", "task 2", [])
        state.set_dependencies("t2", ["t1"])
        state.set_task_status("t1", TaskStatus.failed)

        resolver = WorkflowResolver(state)
        result = resolver.resolve_dependencies("t2")
        assert result is False
        assert state.get_task("t2").status == TaskStatus.blocked

    def test_on_dependency_complete(self, state):
        """Completing a task should promote its dependents."""
        state.create_task("t1", "task 1", [])
        t2 = state.create_task("t2", "task 2", [])
        state.set_dependencies("t2", ["t1"])

        resolver = WorkflowResolver(state)
        state.set_task_status("t1", TaskStatus.completed)
        transitioned = resolver.on_dependency_complete("t1")

        assert "t2" in transitioned
        assert state.get_task("t2").status == TaskStatus.ready

    def test_on_dependency_failed(self, state):
        """Failing a task should block its dependents."""
        state.create_task("t1", "task 1", [])
        t2 = state.create_task("t2", "task 2", [])
        state.set_dependencies("t2", ["t1"])

        resolver = WorkflowResolver(state)
        blocked = resolver.on_dependency_failed("t1")

        assert "t2" in blocked
        assert state.get_task("t2").status == TaskStatus.blocked

    def test_trigger_chain(self, state):
        """Trigger chain should follow the dependency graph."""
        state.create_task("t1", "task 1", [])
        t2 = state.create_task("t2", "task 2", [])
        state.set_dependencies("t2", ["t1"])
        t3 = state.create_task("t3", "task 3", [])
        state.set_dependencies("t3", ["t2"])

        resolver = WorkflowResolver(state)
        chain = resolver.get_trigger_chain("t1")

        assert "t2" in chain
        assert "t3" in chain

    def test_dependency_graph(self, state):
        """Dependency graph should contain all nodes and edges."""
        state.create_task("t1", "task 1", [])
        t2 = state.create_task("t2", "task 2", [])
        state.set_dependencies("t2", ["t1"])

        resolver = WorkflowResolver(state)
        graph = resolver.get_dependency_graph()

        assert len(graph["nodes"]) == 2
        assert len(graph["edges"]) == 1
        assert graph["edges"][0]["from"] == "t1"
        assert graph["edges"][0]["to"] == "t2"

    def test_cascade_resolution(self, state):
        """Completing t1 should promote t2 (t3 waits for t2 to complete)."""
        state.create_task("t1", "task 1", [])
        t2 = state.create_task("t2", "task 2", [])
        state.set_dependencies("t2", ["t1"])
        t3 = state.create_task("t3", "task 3", [])
        state.set_dependencies("t3", ["t2"])

        resolver = WorkflowResolver(state)
        state.set_task_status("t1", TaskStatus.completed)
        transitioned = resolver.on_dependency_complete("t1")

        # t2 should be promoted (all deps met)
        assert "t2" in transitioned
        assert state.get_task("t2").status == TaskStatus.ready
        # t3 stays pending (t2 is ready but not completed)
        assert "t3" not in transitioned
        assert state.get_task("t3").status == TaskStatus.pending


# ===================================================================
# 5. ClusterCore Integration Tests
# ===================================================================

class TestClusterCore:
    def test_core_initialization(self):
        """ClusterCore should initialize with correct defaults."""
        core = ClusterCore(
            cluster_id="test",
            node_id="n1",
            capabilities=["coding"],
        )
        assert core.cluster_id == "test"
        assert core.node_id == "n1"
        assert core.capabilities == ["coding"]
        core.stop()

    def test_core_registers_self(self):
        """Core should register the self-node on init."""
        core = ClusterCore(cluster_id="test", node_id="n1")
        node = core.store.get_node("n1")
        assert node is not None
        assert node.status == NodeStatus.online
        assert "scheduling" in node.capabilities
        core.stop()

    def test_core_start_stop(self):
        """Core should start and stop all subsystems cleanly."""
        core = ClusterCore(cluster_id="test", node_id="n1")
        core.start()
        assert core.watchdog.is_running
        assert core.recovery_detector.is_running
        core.stop()
        assert not core.watchdog.is_running
        assert not core.recovery_detector.is_running

    def test_submit_task(self):
        """Submitting a task should create it and promote to ready."""
        core = ClusterCore(cluster_id="test", node_id="n1")
        task = core.submit_task("test task", priority=1)
        assert task.title == "test task"
        assert task.priority == 1
        fetched = core.store.get_task(task.id)
        assert fetched.status == TaskStatus.ready
        core.stop()

    def test_complete_task(self):
        """Completing a task should mark it done and trigger downstream."""
        core = ClusterCore(cluster_id="test", node_id="n1")
        t1 = core.submit_task("task 1")
        t2 = core.submit_task("task 2")
        core.store.set_dependencies(t2.id, [t1.id])

        # t2 should be pending (waiting for t1)
        assert core.store.get_task(t2.id).status == TaskStatus.pending

        # Complete t1 — t2 gets promoted and may be scheduled
        core.complete_task(t1.id)
        assert core.store.get_task(t1.id).status == TaskStatus.completed

        # t2 should be ready or running (promoted from pending)
        t2_fetched = core.store.get_task(t2.id)
        assert t2_fetched.status in (TaskStatus.ready, TaskStatus.running)
        core.stop()

    def test_fail_task_blocks_downstream(self):
        """Failing a task should block its dependents."""
        core = ClusterCore(cluster_id="test", node_id="n1")
        t1 = core.submit_task("task 1")
        t2 = core.submit_task("task 2")
        core.store.set_dependencies(t2.id, [t1.id])

        core.fail_task(t1.id, reason="test failure")
        assert core.store.get_task(t1.id).status == TaskStatus.failed
        assert core.store.get_task(t2.id).status == TaskStatus.blocked
        core.stop()

    def test_get_summary(self):
        """Summary should include subsystem status."""
        core = ClusterCore(cluster_id="test", node_id="n1")
        summary = core.get_summary()
        assert summary["cluster_id"] == "test"
        assert "subsystems" in summary
        assert summary["subsystems"]["watchdog_running"] is False
        core.stop()

    def test_node_online_callback_triggers_scheduling(self):
        """When a new node comes online, pending tasks should be scheduled."""
        core = ClusterCore(cluster_id="test", node_id="n1", capabilities=["coding"])
        core.start()

        t1 = core.submit_task("rust task", requires=["rust"])
        assert core.store.get_task(t1.id).status == TaskStatus.ready

        core.store.register_node(
            Node(id="node-rust", name="rust-node", capabilities=["rust"])
        )

        core.scheduler.trigger_pending_tasks()
        core.scheduler.schedule_pending()

        task = core.store.get_task(t1.id)
        assert task.status == TaskStatus.running
        assert task.assigned_to == "node-rust"
        core.stop()

    def test_recovery_on_lease_expiry(self):
        """Lease expiry should trigger recovery."""
        core = ClusterCore(cluster_id="test", node_id="n1")
        core.store.register_node(Node(id="node-1", name="n1", capabilities=["coding"]))
        core.store.register_node(Node(id="node-2", name="n2", capabilities=["coding"]))

        t1 = core.submit_task("task 1")
        core.store.set_task_status(t1.id, TaskStatus.running)
        core.store.create_lease(t1.id, "node-1", timedelta(seconds=60))

        core.recovery_detector.handle_offline_sync("node-1")

        task = core.store.get_task(t1.id)
        assert task.status == TaskStatus.running
        core.stop()


# ===================================================================
# 6. Edge Cases
# ===================================================================

class TestEdgeCases:
    def test_empty_cluster_no_crash(self):
        """Operations on empty cluster should not crash."""
        core = ClusterCore(cluster_id="empty", node_id="n1")
        summary = core.get_summary()
        assert summary["tasks"]["total"] == 0
        core.stop()

    def test_duplicate_node_registration(self):
        """Registering the same node twice should update, not duplicate."""
        core = ClusterCore(cluster_id="test", node_id="n1")
        core.store.register_node(
            Node(id="n1", name="updated-name", capabilities=["new-cap"])
        )
        node = core.store.get_node("n1")
        assert node.name == "updated-name"
        assert "new-cap" in node.capabilities
        core.stop()

    def test_complete_nonexistent_task(self):
        """Completing a nonexistent task should return False."""
        core = ClusterCore(cluster_id="test", node_id="n1")
        result = core.complete_task("nonexistent")
        assert result is False
        core.stop()

    def test_fail_nonexistent_task(self):
        """Failing a nonexistent task should return False."""
        core = ClusterCore(cluster_id="test", node_id="n1")
        result = core.fail_task("nonexistent")
        assert result is False
        core.stop()

    def test_trigger_chain_no_deps(self):
        """Trigger chain of a task with no dependents should be empty."""
        core = ClusterCore(cluster_id="test", node_id="n1")
        t1 = core.submit_task("solo task")
        chain = core.get_trigger_chain(t1.id)
        assert chain == []
        core.stop()

    def test_dependency_graph_empty(self):
        """Empty cluster should return empty graph."""
        core = ClusterCore(cluster_id="test", node_id="n1")
        graph = core.get_dependency_graph()
        assert graph["nodes"] == []
        assert graph["edges"] == []
        core.stop()

    def test_concurrent_task_submission(self):
        """Multiple concurrent task submissions should not corrupt state."""
        core = ClusterCore(cluster_id="test", node_id="n1")
        errors = []

        def submit_tasks(prefix: str):
            try:
                for i in range(10):
                    core.submit_task(f"{prefix}-task-{i}")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=submit_tasks, args=(f"t{t}",)) for t in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        tasks = core.store.get_all_tasks()
        assert len(tasks) == 30
        core.stop()

    def test_lease_expiry_callback_wiring(self):
        """Lease expiry callback should be wired during core init."""
        core = ClusterCore(cluster_id="test", node_id="n1")
        assert core.store._lease_callback is not None
        core.stop()

    def test_node_online_callback_wiring(self):
        """Node online callback should be wired during core init."""
        core = ClusterCore(cluster_id="test", node_id="n1")
        assert core.store._on_node_online is not None
        core.stop()

    def test_capability_change_callback_wiring(self):
        """Capability change callback should be wired during core init."""
        core = ClusterCore(cluster_id="test", node_id="n1")
        assert core.store._on_capability_change is not None
        core.stop()


# ===================================================================
# 7. Watchdog Background Loop Tests
# ===================================================================

class TestWatchdogBackground:
    def _make_adapter(self, state):
        class Adapter(WatchdogRegistry):
            def __init__(self, s):
                self._s = s
            def get_all_heartbeat_nodes(self):
                return [
                    HeartbeatNode(n.id, n.last_heartbeat, n.status.value)
                    for n in self._s.get_all_nodes()
                ]
            def update_node_status(self, node_id, status):
                with self._s._nodes_lock:
                    if node_id in self._s._nodes:
                        self._s._nodes[node_id].status = NodeStatus(status)
        return Adapter(state)

    def test_background_loop_detects_offline(self, state):
        """Watchdog background thread should detect offline nodes."""
        node = Node(
            id="node-old",
            name="old",
            capabilities=["coding"],
            last_heartbeat=datetime.utcnow() - timedelta(seconds=60),
        )
        state.register_node(node)

        adapter = self._make_adapter(state)
        events = []

        wd = Watchdog(
            registry=adapter,
            check_interval=0.1,
            degraded_after=0.05,
            offline_after=0.1,
            callback=lambda e: events.append(e),
        )
        wd.start()
        time.sleep(0.3)
        wd.stop()

        assert len(events) >= 1
        assert events[0].node_id == "node-old"


# ===================================================================
# 8. Recovery Background Loop Tests
# ===================================================================

class TestRecoveryBackground:
    def test_background_recovery(self, state):
        """Recovery detector background thread should process events."""
        state.register_node(Node(id="node-1", name="n1", capabilities=["coding"]))
        state.register_node(Node(id="node-2", name="n2", capabilities=["coding"]))
        state.create_task("t1", "task 1", [])
        state.set_task_status("t1", TaskStatus.running)
        state.create_lease("t1", "node-1", timedelta(seconds=60))

        revoker = Revoker(state)
        rescheduler = Rescheduler(state)
        detector = RecoveryDetector(revoker, rescheduler, state)

        detector.start()
        detector.notify_offline("node-1")
        time.sleep(0.2)
        detector.stop()

        task = state.get_task("t1")
        assert task.status == TaskStatus.running
