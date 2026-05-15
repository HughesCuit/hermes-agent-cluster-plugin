# Changelog

All notable changes to hermes-agent-cluster-plugin are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

---

## [1.0.0] — 2026-05-14

### Added
- Plugin SDK (Webhook) — external services can listen to cluster task events via registered webhook URLs
- Dynamic scheduler — load-aware task routing with priority queue and scoring
- Multi-cluster federation — route tasks between independent clusters for cross-cluster workloads
- WAN cluster support — TLS encryption, auto-reconnect with exponential backoff, batch event sync
- Dashboard configuration panel — edit capabilities, view cluster config, restart service from UI
- Full Hermes Admin Dashboard integration — "Cluster" tab with 17 REST API proxy endpoints

### Changed
- Go binary version: v1.0.0 stable release
- Dashboard React UI: unified views for Config, Status, Nodes, Tasks, Leases
- plugin_api.py: 17 FastAPI routes for complete cluster management
- Enhanced error handling in `__init__.py` for binary detection and startup

---

## [0.8.0] — 2026-05-10

### Added
- Web Dashboard with FastAPI backend (`dashboard/plugin_api.py`) for real-time cluster monitoring
- OpenTelemetry tracing support (OTLP exporter)
- Prometheus metrics endpoint (`/metrics`)
- Task dependency chains — tasks with `requires` field auto-advance when dependencies complete
- Task leases with configurable TTL to prevent duplicate execution
- Dynamic capability updates — runtime changes trigger automatic re-scheduling

### Changed
- Improved watchdog: degraded/offline detection with configurable thresholds
- Enhanced node heartbeat protocol with load reporting

---

## [0.7.0] — 2026-04-20

### Added
- Capability-aware task scheduling — tasks routed to nodes matching required capabilities
- Fault detection and automatic task re-dispatch when nodes go offline
- `kanban_cluster_heartbeat` tool for node liveness reporting

### Changed
- Improved error messages for connection failures

---

## [0.6.0] — 2026-03-25

### Added
- Task dependency support via `requires` field in `kanban_cluster_submit`
- Automatic workflow progression when dependencies are met

### Fixed
- Race condition in task assignment under high concurrency

---

## [0.5.0] — 2026-03-01

### Added
- `kanban_cluster_complete` tool to mark tasks done with results
- `kanban_cluster_list` now returns task status, assignee, and timestamps
- Cluster auth token support for secure multi-node setups

---

## [0.4.0] — 2026-02-10

### Added
- `kanban_cluster_nodes` tool to list all registered cluster nodes
- Node capabilities reporting in node list

### Changed
- Improved install script with `--download` and `--from` options

---

## [0.3.0] — 2026-01-20

### Added
- `kanban_cluster_submit` tool to submit tasks to the cluster
- `kanban_cluster_list` tool to view pending/active tasks
- Task routing to available worker nodes

### Changed
- Binary auto-build from source when Go is available

---

## [0.2.0] — 2025-12-15

### Added
- `kanban_cluster_join` tool — worker nodes can join an existing cluster
- Worker node registers capabilities and receives task assignments
- Install script with GitHub Releases download option

### Fixed
- Plugin not loading when Go binary is missing (graceful degradation)

---

## [0.1.0] — 2025-11-20

### Added
- Initial release
- `kanban_cluster_init` tool — initialize a new cluster as the main/coordinator node
- Auto-build `hermes-cluster` Go binary from source
- Basic cluster configuration via `~/.hermes/agent-cluster/cluster.yaml`
- Plugin registration with Hermes Agent tool system
- Install script for Go binary compilation
- `cluster.yaml.example` configuration template

---

# 更新日志

所有 hermes-agent-cluster-plugin 的重要变更记录。
格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

---

## [1.0.0] — 2026-05-14

### 新增
- Plugin SDK (Webhook) — 外部服务可注册 Webhook 监听集群任务事件
- 动态调度器 — 负载感知路由 + 优先级队列 + 评分算法
- 多集群联邦 — 跨集群任务路由与协调
- WAN 集群支持 — TLS 加密、自动重连（指数退避）、批量事件同步
- Dashboard 配置面板 — 图形化编辑 capabilities、查看配置、重启服务
- 完整 Hermes Admin Dashboard 集成 — "Cluster" 标签页，17 个 REST API 代理端点

### 变更
- Go 二进制版本：v1.0.0 稳定版
- Dashboard React UI：统一视图（Config / Status / Nodes / Tasks / Leases）
- plugin_api.py：17 个 FastAPI 路由，完整集群管理
- 增强 `__init__.py` 二进制检测与启动的错误处理

---

## [0.8.0] — 2026-05-10

### 新增
- Web Dashboard，FastAPI 后端（`dashboard/plugin_api.py`），实时查看集群状态
- OpenTelemetry 追踪支持（OTLP exporter）
- Prometheus 指标端点（`/metrics`）
- 任务依赖链 — `requires` 字段的任务在依赖完成后自动推进
- 任务租约，可配置 TTL 防止重复执行
- 动态能力更新 — 运行时变更触发自动重调度

### 变更
- 改进 watchdog：可配置的降级/离线检测阈值
- 增强节点心跳协议，支持负载上报

---

## [0.7.0] — 2026-04-20

### 新增
- 能力感知任务调度 — 按节点 capabilities 路由任务
- 故障检测与任务自动重派
- `kanban_cluster_heartbeat` 心跳工具

### 变更
- 改进连接失败的错误提示

---

## [0.6.0] — 2026-03-25

### 新增
- 任务依赖支持（`requires` 字段）
- 依赖满足后自动推进工作流

### 修复
- 高并发下的任务分配竞态条件

---

## [0.5.0] — 2026-03-01

### 新增
- `kanban_cluster_complete` 标记任务完成并附带结果
- `kanban_cluster_list` 返回任务状态、执行者和时间戳
- 集群认证 token 支持

---

## [0.4.0] — 2026-02-10

### 新增
- `kanban_cluster_nodes` 列出所有集群节点
- 节点能力信息展示

### 变更
- 安装脚本增加 `--download` 和 `--from` 选项

---

## [0.3.0] — 2026-01-20

### 新增
- `kanban_cluster_submit` 提交任务到集群
- `kanban_cluster_list` 查看待办/执行中任务
- 任务路由到可用工作节点

### 变更
- 有 Go 环境时自动从源码构建二进制

---

## [0.2.0] — 2025-12-15

### 新增
- `kanban_cluster_join` 工具 — 工作节点加入已有集群
- 工作节点注册能力并接收任务分配
- 安装脚本支持 GitHub Releases 下载

### 修复
- Go 二进制缺失时插件加载失败（优雅降级）

---

## [0.1.0] — 2025-11-20

### 新增
- 首次发布
- `kanban_cluster_init` 工具 — 初始化集群（主节点/协调者）
- 自动从源码构建 `hermes-cluster` Go 二进制
- 基础集群配置（`~/.hermes/agent-cluster/cluster.yaml`）
- 插件注册到 Hermes Agent 工具系统
- Go 二进制编译安装脚本
- `cluster.yaml.example` 配置模板
## [1.2.0] — 2026-05-14

### Added
- Config Management API (GET/PUT /config, /config/validate, /config/yaml, /config/restart)
- Dashboard Config page with full configuration management UI
- Hot restart endpoint
