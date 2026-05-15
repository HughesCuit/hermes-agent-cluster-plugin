# hermes-agent-cluster-plugin

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Plugin](https://img.shields.io/badge/Plugin-v1.0.0-orange)

**Hermes Agent cluster coordination plugin** — install with one command, get multiple Hermes Agent instances working together.

```bash
hermes plugins install HughesCuit/hermes-agent-cluster-plugin
```

---

## What Is This

A [Hermes Agent](https://github.com/nousresearch/hermes-agent) plugin that registers 7 `kanban_cluster_*` tools for distributed multi-node task coordination.

It works with the **hermes-cluster** Go binary service (auto-built from [hermes-agent-cluster](https://github.com/HughesCuit/hermes-agent-cluster)) to provide:

- **Multi-node task coordination** — main node assigns, worker nodes execute
- **Capability-aware scheduling** — tasks routed based on node capabilities
- **Dynamic capability updates** — runtime changes trigger automatic re-scheduling
- **Task dependency chains** — workflows advance automatically
- **Task leases** — prevents duplicate execution
- **Fault detection & recovery** — offline nodes trigger automatic re-dispatch
- **Dashboard** — Web UI for real-time cluster status
- **Prometheus metrics** + **OpenTelemetry tracing**

---

## Installation

### Method 1: Hermes CLI Install (Recommended)

```bash
hermes plugins install HughesCuit/hermes-agent-cluster-plugin
```

Then run the install script to auto-compile the Go binary:

```bash
bash ~/.hermes/plugins/hermes-agent-cluster/install.sh
```

> Requires Go 1.22+. If you don't want to install Go, use `--download` to try downloading a pre-built binary:
> ```bash
> bash ~/.hermes/plugins/hermes-agent-cluster/install.sh --download
> ```

### Method 2: Git URL Install

```bash
hermes plugins install https://github.com/HughesCuit/hermes-agent-cluster-plugin.git
bash ~/.hermes/plugins/hermes-agent-cluster/install.sh
```

### Method 3: Source Install

```bash
git clone https://github.com/HughesCuit/hermes-agent-cluster-plugin.git
cd hermes-agent-cluster-plugin
bash install.sh
```

### Method 4: Docker (Hermes Agent + Plugin Containerized)

If running Hermes Agent via Docker, install inside the container:

```dockerfile
FROM hermes-agent:latest
RUN hermes plugins install HughesCuit/hermes-agent-cluster-plugin
COPY install.sh /tmp/
RUN bash /tmp/install.sh
```

Or use docker-compose for multi-node deployment (see `docker-compose.hermes.yml` in the main repo).

---

## Post-Install

Restart Hermes Agent and the plugin loads automatically. Use `kanban_cluster_init` to start a cluster.

Or start manually:

```bash
# Main node
hermes-cluster -config ~/.hermes/agent-cluster/cluster.yaml

# Worker node (edit role and endpoint in cluster.yaml)
hermes-cluster -config ~/.hermes/agent-cluster/cluster.yaml
```

---

## Tools

| Tool | Description |
|------|-------------|
| `kanban_cluster_init` | 🏗️ Initialize a new cluster (main/coordinator node) |
| `kanban_cluster_join` | 🔗 Join an existing cluster as a worker node |
| `kanban_cluster_submit` | 📋 Submit a task to the cluster for distributed execution |
| `kanban_cluster_list` | 📊 List all tasks in the cluster |
| `kanban_cluster_nodes` | 🖥️ List all nodes in the cluster |
| `kanban_cluster_heartbeat` | 💓 Send heartbeat to indicate this node is alive |
| `kanban_cluster_complete` | ✅ Mark a task as completed with results |

---

## Architecture

```
                    ┌──────────────────┐
                    │   Main Node      │
                    │──────────────────│
                    │ Cluster Registry │
                    │ Lease Manager    │
                    │ Task Router      │
                    │ Remote API       │
                    └────────┬─────────┘
                             │ HTTP
          ┌──────────────────┼──────────────────┐
          │                  │                  │
    ┌─────┴──────┐    ┌─────┴──────┐    ┌─────┴──────┐
    │ PC Node    │    │ NAS Node   │    │ VPS Node   │
    │ coding,gpu │    │ planner    │    │ research   │
    └────────────┘    └────────────┘    └────────────┘
```

Each node runs Hermes Agent independently and coordinates via HTTP API. Nodes sync only task metadata and events — **no shared database**.

---

## Configuration

See `cluster.yaml.example` for a full reference:

```yaml
cluster:
  id: my-cluster
  role: main        # "main" or "worker"
  endpoint: ""      # worker: main node URL (e.g. "http://server:8787")

node:
  id: node_main
  capabilities:
    - planning
    - reviewing

server:
  bind: "0.0.0.0"
  port: 8787

lease:
  ttl: 30s
  scan_rate: 5s

watchdog:
  check_interval: 3s
  degraded_after: 10s
  offline_after: 20s

telemetry:
  enabled: false
  exporter: "none"          # "otlp", "stdout", "none"
  endpoint: "localhost:4317"
  service_name: "hermes-agent-cluster"
  sample_rate: 1.0
  batch_timeout: 5s
```

---

## FAQ

### Q: Why do I need Go?

`hermes-cluster` is the cluster coordination service written in Go. The plugin itself is pure Python (no dependencies), but starting a cluster requires the Go binary.

### Q: Can I install without Go?

Try `bash install.sh --download` — it attempts to download a pre-built binary from GitHub Releases. If no binary is available for your platform, you'll need Go 1.22+.

### Q: Tools don't appear after installation?

Make sure you restarted Hermes Agent. Run `hermes plugins list` to verify the plugin is loaded.

### Q: Can I install the plugin without running a cluster?

Yes. The plugin registers tools but the cluster process only starts when you call `kanban_cluster_init` or `kanban_cluster_join`.

### Q: How do I add more nodes?

On each new machine, run `kanban_cluster_join` with the main node's endpoint URL. Set appropriate capabilities for that node.

---

## Links

- [hermes-agent-cluster](https://github.com/HughesCuit/hermes-agent-cluster) — Go cluster service source
- [Hermes Agent](https://github.com/nousresearch/hermes-agent) — Hermes Agent main project
- [SPEC.md](https://github.com/HughesCuit/hermes-agent-cluster/blob/main/SPEC.md) — Full architecture design doc

---

## License

MIT — Copyright (c) Heventure Group

---
---

# hermes-agent-cluster-plugin （中文）

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Plugin](https://img.shields.io/badge/Plugin-v1.0.0-orange)

**Hermes 官方集群插件** — 一行命令安装，让多个 Hermes Agent 实例协同工作。

```bash
hermes plugins install HughesCuit/hermes-agent-cluster-plugin
```

---

## 这是什么

这是一个 [Hermes Agent](https://github.com/nousresearch/hermes-agent) 插件，提供 7 个 `kanban_cluster_*` 工具，用于跨机器分布式任务协调。

配合 **hermes-cluster** Go 二进制服务（从 [hermes-agent-cluster](https://github.com/HughesCuit/hermes-agent-cluster) 自动构建），实现：

- **多机任务协同** — 主节点分配，工作节点执行
- **能力感知调度** — 根据节点 capabilities 智能分配任务
- **动态能力更新** — 运行时更新，自动重调度
- **任务依赖链** — 工作流自动推进
- **任务租约** — 防重复执行
- **故障检测与恢复** — 节点离线自动重派
- **Dashboard** — Web UI 实时查看集群状态
- **Prometheus 指标** + **OpenTelemetry 追踪**

---

## 安装

### 方式一：Hermes CLI 安装（推荐）

```bash
hermes plugins install HughesCuit/hermes-agent-cluster-plugin
```

然后运行安装脚本，自动编译 Go 二进制：

```bash
bash ~/.hermes/plugins/hermes-agent-cluster/install.sh
```

> 需要安装 Go 1.22+。如果不想装 Go，用 `--download` 参数尝试下载预编译包：
> ```bash
> bash ~/.hermes/plugins/hermes-agent-cluster/install.sh --download
> ```

### 方式二：从 Git URL 安装

```bash
hermes plugins install https://github.com/HughesCuit/hermes-agent-cluster-plugin.git
bash ~/.hermes/plugins/hermes-agent-cluster/install.sh
```

### 方式三：从源码安装

```bash
git clone https://github.com/HughesCuit/hermes-agent-cluster-plugin.git
cd hermes-agent-cluster-plugin
bash install.sh
```

### 方式四：Docker 方式（Hermes Agent + 插件容器化）

如果通过 Docker 运行 Hermes Agent，可以用以下方式在容器内安装：

```dockerfile
FROM hermes-agent:latest
RUN hermes plugins install HughesCuit/hermes-agent-cluster-plugin
COPY install.sh /tmp/
RUN bash /tmp/install.sh
```

或者使用 docker-compose 多节点部署（参考主仓库的 `docker-compose.hermes.yml`）。

---

## 安装后

重启 Hermes Agent，插件会自动加载。你可以用 `kanban_cluster_init` 启动集群。

或者手动启动：

```bash
# 主节点
hermes-cluster -config ~/.hermes/agent-cluster/cluster.yaml

# 工作节点（修改 cluster.yaml 中的 role 和 endpoint）
hermes-cluster -config ~/.hermes/agent-cluster/cluster.yaml
```

---

## 工具列表

| 工具 | 说明 |
|------|------|
| `kanban_cluster_init` | 🏗️ 初始化集群（主节点/协调者） |
| `kanban_cluster_join` | 🔗 加入集群（工作节点） |
| `kanban_cluster_submit` | 📋 提交任务到集群执行 |
| `kanban_cluster_list` | 📊 列出集群所有任务 |
| `kanban_cluster_nodes` | 🖥️ 列出集群所有节点 |
| `kanban_cluster_heartbeat` | 💓 发送心跳保持在线 |
| `kanban_cluster_complete` | ✅ 标记任务完成并附带结果 |

---

## 架构

```
                    ┌──────────────────┐
                    │   主节点          │
                    │──────────────────│
                    │ 集群注册中心       │
                    │ 租约管理器         │
                    │ 任务路由器         │
                    │ 远程 API          │
                    └────────┬─────────┘
                             │ HTTP
          ┌──────────────────┼──────────────────┐
          │                  │                  │
    ┌─────┴──────┐    ┌─────┴──────┐    ┌─────┴──────┐
    │ PC 节点    │    │ NAS 节点   │    │ VPS 节点   │
    │ 编码,GPU   │    │ 规划       │    │ 研究       │
    └────────────┘    └────────────┘    └────────────┘
```

每个节点独立运行 Hermes Agent，通过 HTTP API 进行集群协调。节点之间仅同步任务元数据和事件，**不共享数据库**。

---

## 配置参考

配置文件示例：`cluster.yaml.example`

```yaml
cluster:
  id: my-cluster
  role: main        # "main" 或 "worker"
  endpoint: ""      # worker 节点填写主节点地址

node:
  id: node_main
  capabilities:
    - planning
    - reviewing

server:
  bind: "0.0.0.0"
  port: 8787

lease:
  ttl: 30s
  scan_rate: 5s

watchdog:
  check_interval: 3s
  degraded_after: 10s
  offline_after: 20s

telemetry:
  enabled: false
  exporter: "none"          # "otlp", "stdout", "none"
  endpoint: "localhost:4317"
  service_name: "hermes-agent-cluster"
  sample_rate: 1.0
  batch_timeout: 5s
```

---

## 常见问题

### Q: 为什么需要 Go？

`hermes-cluster` 是用 Go 写的集群协调服务。插件本身是纯 Python（无额外依赖），但启动集群需要 Go 二进制。

### Q: 不用 Go 能装吗？

可以试试 `bash install.sh --download`，会从 GitHub Releases 下载预编译二进制（如果有的话）。目前 Release 没有传二进制文件，欢迎 PR。

### Q: 插件装好后看不到工具？

确保你重启了 Hermes Agent。运行 `hermes plugins list` 检查是否已加载。

### Q: 能不能只装插件不用集群？

可以。插件注册的工具只有在调用 `kanban_cluster_init` 或 `kanban_cluster_join` 后才会启动集群进程。

### Q: 怎么添加更多节点？

在每台新机器上运行 `kanban_cluster_join`，传入主节点的 endpoint 地址。为每台节点设置合适的能力（capabilities）。

---

## 相关资源

- [hermes-agent-cluster](https://github.com/HughesCuit/hermes-agent-cluster) — Go 集群服务源码
- [Hermes Agent](https://github.com/nousresearch/hermes-agent) — Hermes Agent 主项目
- [SPEC.md](https://github.com/HughesCuit/hermes-agent-cluster/blob/main/SPEC.md) — 完整架构设计文档

---

## 许可证

MIT — Copyright (c) Heventure Group
