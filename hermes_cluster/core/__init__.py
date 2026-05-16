"""Core cluster orchestration — the Python equivalent of Go's cmdServe.

Wires up all subsystems:
  - State management (ClusterStore or ClusterState)
  - Scheduler (task assignment to nodes)
  - Recovery (detector, revoker, rescheduler)
  - Heartbeat watchdog (node health monitoring)
  - Background services lifecycle

Usage:
    core = ClusterCore(db_path="cluster.db")
    core.start()
    # ... use API ...
    core.stop()
"""
