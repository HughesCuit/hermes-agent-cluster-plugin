# hermes-agent-cluster-plugin

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python&logoColor=white)
![License](https://img.shields.io/badge/License-MIT-green)
![Plugin](https://img.shields.io/badge/Plugin-v2.0.0-orange)

**Hermes Agent 分布式集群协调插件** — 一行命令安装，多个 Hermes Agent 实例自动协同工作。

```bash
hermes plugins install HughesCuit/hermes-agent-cluster-plugin
```

---

## 这是什么

一个 [Hermes Agent](https://github.com/nousresearch/hermes-agent) 插件，注册 7 个 `kanban_cluster_*` 工具，实现分布式多节点任务协调。

**纯 Python 实现，不需要 Go 编译，不需要下载二进制。** 安装后重启 Hermes 即可使用。

功能：
- **多节点任务协调** — 主节点分配，工作节点执行
- **能力感知调度** — 按节点能力（coding/gpu/browser 等）自动路由任务
- **任务依赖链** — 工作流自动推进（A→B→C）
- **租约管理** — 防止重复执行
- **故障检测与恢复** — 节点离线自动重调度
- **Web Dashboard** — 实时集群状态可视化
- **Plugin SDK** — Webhook 钩子系统
- **多集群联邦** — 跨集群任务转发
- **Prometheus 指标** + **OpenTelemetry 追踪**

---

## 安装

### 方式一：Hermes CLI（推荐）

```bash
# 1. 安装插件
hermes plugins install HughesCuit/hermes-agent-cluster-plugin

# 2. 重启 Hermes Agent
hermes gateway restart

# 3. 验证（在 Hermes 对话中）
# 使用 kanban_cluster_init 工具初始化集群
```

### 方式二：Dashboard 安装

在 Hermes Admin Dashboard → Plugins → "Install from Git" → 输入：

```
HughesCuit/hermes-agent-cluster-plugin
```

然后重启 Hermes。

### 方式三：手动安装

```bash
git clone https://github.com/HughesCuit/hermes-agent-cluster-plugin.git
cp -r hermes-agent-cluster-plugin/ ~/.hermes/plugins/hermes-agent-cluster/
hermes gateway restart
```

---

## 快速开始

### 1. 初始化集群（主节点）

```bash
# 在 Hermes 对话中使用工具
kanban_cluster_init \
  --cluster_id my-cluster \
  --role main \
  --node_name main-node \
  --capabilities '["planner","reviewer","scheduler"]'
```

### 2. 添加工作节点

在另一台机器上：

```bash
kanban_cluster_join \
  --endpoint http://<主节点IP>:8787 \
  --node_name worker-node \
  --capabilities '["coding","gpu","browser"]'
```

### 3. 提交任务

```bash
kanban_cluster_submit \
  --title "实现用户认证模块" \
  --required_capabilities '["coding"]' \
  --priority 1
```

### 4. 查看集群状态

```bash
kanban_cluster_list
kanban_cluster_nodes
```

---

## 插件工具

| 工具 | 说明 |
|------|------|
| `kanban_cluster_init` | 初始化集群（创建主节点） |
| `kanban_cluster_join` | 加入已有集群 |
| `kanban_cluster_submit` | 提交任务（自动调度到匹配节点） |
| `kanban_cluster_list` | 查看任务列表 |
| `kanban_cluster_nodes` | 查看节点状态 |
| `kanban_cluster_heartbeat` | 发送心跳（保持节点在线） |
| `kanban_cluster_complete` | 标记任务完成 |

---

## Dashboard

插件安装后，Hermes Admin Dashboard 侧边栏会出现 **"Cluster"** 标签页：

- **Overview** — 节点状态、任务统计、租约信息
- **Nodes** — 节点列表、capabilities、负载、心跳
- **Tasks** — 任务列表、状态、分配节点、依赖链
- **Config** — 集群配置编辑器

访问方式：`http://127.0.0.1:8888` → 侧边栏 "Cluster"

---

## 架构

```
┌─────────────────────────────────────────────┐
│                Hermes Agent                  │
│  ┌─────────────────────────────────────────┐│
│  │  hermes_cluster/ (Python)              ││
│  │  ├── state/cluster_store.py (SQLite)   ││
│  │  ├── core/cluster_core.py (调度+工作流) ││
│  │  ├── core/watchdog.py (心跳监控)       ││
│  │  ├── core/recovery.py (故障恢复)       ││
│  │  └── models/ (Pydantic 模型)          ││
│  └─────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────┐│
│  │  __init__.py (7 个 kanban_cluster_* 工具)││
│  │  dashboard/plugin_api.py (FastAPI 路由)  ││
│  └─────────────────────────────────────────┘│
│  ┌─────────────────────────────────────────┐│
│  │  dashboard/dist/index.js (React SPA)   ││
│  └─────────────────────────────────────────┘│
└─────────────────────────────────────────────┘
```

**没有外部进程，没有 Go 二进制，没有 HTTP 代理。** 所有逻辑在 Hermes Agent 进程内运行，通过 SQLite 持久化。

---

## 开发

### 运行测试

```bash
cd ~/.hermes/plugins/hermes-agent-cluster
python3 -m pytest tests/ -v
```

### 项目结构

```
hermes-agent-cluster-plugin/
├── plugin.yaml                    # 插件清单
├── __init__.py                    # 插件入口（register + 工具定义）
├── hermes_cluster/
│   ├── core/
│   │   ├── cluster_core.py        # 核心调度 + 工作流
│   │   ├── watchdog.py            # 心跳监控
│   │   └── recovery.py            # 故障恢复
│   ├── state/
│   │   └── cluster_store.py       # SQLite 存储层
│   └── models/
│       └── __init__.py            # Pydantic 数据模型
├── dashboard/
│   ├── manifest.json              # Dashboard 清单
│   ├── plugin_api.py              # FastAPI 路由
│   └── dist/index.js              # React SPA
├── tests/
│   ├── test_cluster_store.py      # 存储层测试 (64)
│   ├── test_cluster_core.py       # 核心逻辑测试 (47)
│   ├── test_tools.py              # 工具测试 (31)
│   ├── test_plugin_api.py         # API 路由测试 (29)
│   └── test_dropin_replacement.py # 替换兼容性测试 (15)
└── CHANGELOG.md
```

### 贡献

见 [CONTRIBUTING.md](CONTRIBUTING.md)。

---

## 常见问题

### Q: 需要安装 Go 吗？

**不需要。** v2.0.0 起，所有逻辑在 Python 中运行，不需要 Go 编译或二进制文件。

### Q: 数据存在哪里？

SQLite 数据库在 `~/.hermes/agent-cluster/cluster.db`，自动创建。

### Q: 多节点怎么通信？

当前版本支持单节点模式。多节点通信需要配合主仓库 [hermes-agent-cluster](https://github.com/HughesCuit/hermes-agent-cluster) 的 Go 服务使用。详见主仓库文档。

### Q: Dashboard 看不到 Cluster 标签？

重启 Hermes Admin：`hermes gateway restart`，然后刷新浏览器。

### Q: 测试跑不过？

确保安装了依赖：`pip install pydantic fastapi`（通常 Hermes Agent 已内置）。

---

## 版本历史

- **v2.0.0** — Python 重写，移除 Go 依赖
- v1.2.1 — GitHub Actions 多平台二进制发布 + 自动下载
- v1.2.0 — Config Management API + Dashboard Config 页面
- v1.1.0 — 插件自动启动 + Dashboard Quick Start 引导页
- v1.0.0 — 稳定版发布
- v0.1.0 — 初始版本（Go 服务 + Python 插件）

---

## 许可证

MIT License. 详见 [LICENSE](LICENSE)。
