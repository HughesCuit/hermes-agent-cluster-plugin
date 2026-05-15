# Contributing to hermes-agent-cluster-plugin

Thank you for your interest in contributing! This guide covers development setup, testing, and pull request process.

---

## Development Setup

### Prerequisites

- Python 3.10+
- Go 1.22+ (for building the `hermes-cluster` binary)
- Git
- Hermes Agent installed locally

### Getting Started

1. Fork and clone the repository:

```bash
git clone https://github.com/YOUR_USERNAME/hermes-agent-cluster-plugin.git
cd hermes-agent-cluster-plugin
```

2. The plugin is a single Python file (`__init__.py`) with no pip dependencies. You can edit it directly.

3. To test with Hermes Agent, symlink into your plugins directory:

```bash
ln -sf $(pwd) ~/.hermes/plugins/hermes-agent-cluster
```

4. Restart Hermes Agent to load your development version.

---

## Project Structure

```
hermes-agent-cluster-plugin/
├── __init__.py              # Plugin entry point — 7 tool handlers + registration
├── plugin.yaml              # Plugin metadata (name, version, hooks)
├── install.sh               # Install script (builds/downloads Go binary)
├── cluster.yaml.example     # Example configuration
├── dashboard/
│   ├── plugin_api.py        # FastAPI router for Hermes Dashboard
│   ├── manifest.json        # Dashboard widget manifest
│   └── dist/index.js        # Dashboard frontend
└── README.md
```

### Key Points

- `__init__.py` is the entire plugin. All 7 `kanban_cluster_*` tool handlers live here.
- The plugin wraps a Go binary (`hermes-cluster`) via `subprocess`. It does not import any third-party Python packages.
- Tool schemas follow the JSON Schema format used by Hermes Agent.

---

## Code Style

- Python 3.10+ syntax (type hints with `X | None`, `dict`, `list`)
- No external dependencies — use only Python standard library
- Log with `logging.getLogger(__name__)`
- Tool handlers return JSON strings (not dicts)
- Follow existing code patterns in `__init__.py`

---

## Testing

### Manual Testing

1. Start a main node:
```bash
# In Hermes Agent, call kanban_cluster_init
# Or manually:
hermes-cluster -config ~/.hermes/agent-cluster/cluster.yaml
```

2. Start a worker on another machine (or same machine, different port):
```bash
# In Hermes Agent, call kanban_cluster_join with the main node endpoint
```

3. Test task flow:
```bash
# Submit a task
kanban_cluster_submit title="Test task" requires=["coding"]
# List tasks
kanban_cluster_list
# Complete a task
kanban_cluster_complete task_id="<id>" result="done"
```

### Automated Testing

The plugin currently relies on integration testing with a running Hermes Agent instance. To add unit tests:

1. Create a `tests/` directory
2. Test tool handlers by mocking the `_api_call` and `_find_or_install_binary` functions
3. Verify JSON schema correctness for each tool

---

## Pull Request Process

1. **Create a branch** from `main`:
```bash
git checkout -b feature/your-feature-name
```

2. **Make your changes** following the code style above.

3. **Update documentation** if adding/changing tools or config options:
   - Update `README.md` (both English and Chinese sections)
   - Update `CHANGELOG.md` with your changes under an appropriate version
   - Update `cluster.yaml.example` if adding config options

4. **Test manually** with Hermes Agent to verify tools work end-to-end.

5. **Commit** with a clear message:
```bash
git commit -m "feat: add awesome new feature"
```

Use conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`.

6. **Push and create a PR**:
```bash
git push origin feature/your-feature-name
```

7. In your PR description:
   - Describe what changed and why
   - Include screenshots if changing Dashboard
   - Reference any related issues

---

## Reporting Issues

Use the GitHub issue templates:
- **Bug Report** — for bugs and unexpected behavior
- **Feature Request** — for new features and improvements

---

## License

By contributing, you agree that your contributions will be licensed under the MIT License.

---
---

# 贡献指南

感谢你有兴趣参与贡献！本指南涵盖开发环境搭建、测试和 PR 流程。

---

## 开发环境搭建

### 前置要求

- Python 3.10+
- Go 1.22+（编译 `hermes-cluster` 二进制）
- Git
- 本地安装 Hermes Agent

### 快速开始

1. Fork 并克隆仓库：

```bash
git clone https://github.com/YOUR_USERNAME/hermes-agent-cluster-plugin.git
cd hermes-agent-cluster-plugin
```

2. 插件是单个 Python 文件（`__init__.py`），无 pip 依赖，可直接编辑。

3. 为了用 Hermes Agent 测试，软链接到插件目录：

```bash
ln -sf $(pwd) ~/.hermes/plugins/hermes-agent-cluster
```

4. 重启 Hermes Agent 加载开发版本。

---

## 项目结构

```
hermes-agent-cluster-plugin/
├── __init__.py              # 插件入口 — 7 个工具处理函数 + 注册
├── plugin.yaml              # 插件元数据（名称、版本、hooks）
├── install.sh               # 安装脚本（编译/下载 Go 二进制）
├── cluster.yaml.example     # 配置示例
├── dashboard/
│   ├── plugin_api.py        # Hermes Dashboard 的 FastAPI 路由
│   ├── manifest.json        # Dashboard 组件清单
│   └── dist/index.js        # Dashboard 前端
└── README.md
```

### 关键点

- `__init__.py` 是整个插件。所有 7 个 `kanban_cluster_*` 工具处理函数都在这里。
- 插件通过 `subprocess` 封装 Go 二进制（`hermes-cluster`），不导入任何第三方 Python 包。
- 工具 schema 使用 Hermes Agent 的 JSON Schema 格式。

---

## 代码风格

- Python 3.10+ 语法（类型注解用 `X | None`、`dict`、`list`）
- 不使用外部依赖 — 仅使用 Python 标准库
- 用 `logging.getLogger(__name__)` 记录日志
- 工具处理函数返回 JSON 字符串（非 dict）
- 遵循 `__init__.py` 中的现有代码模式

---

## 测试

### 手动测试

1. 启动主节点：
```bash
# 在 Hermes Agent 中调用 kanban_cluster_init
# 或手动启动：
hermes-cluster -config ~/.hermes/agent-cluster/cluster.yaml
```

2. 在另一台机器（或同一机器不同端口）启动工作节点：
```bash
# 在 Hermes Agent 中调用 kanban_cluster_join，传入主节点 endpoint
```

3. 测试任务流程：
```bash
# 提交任务
kanban_cluster_submit title="测试任务" requires=["coding"]
# 列出任务
kanban_cluster_list
# 完成任务
kanban_cluster_complete task_id="<id>" result="done"
```

### 自动化测试

插件目前依赖与 Hermes Agent 集成测试。如需添加单元测试：

1. 创建 `tests/` 目录
2. 通过 mock `_api_call` 和 `_find_or_install_binary` 函数测试工具处理函数
3. 验证每个工具的 JSON schema 正确性

---

## PR 流程

1. **创建分支**（基于 `main`）：
```bash
git checkout -b feature/你的功能名
```

2. **编写代码**，遵循上述代码风格。

3. **更新文档**（如添加/更改工具或配置）：
   - 更新 `README.md`（英文和中文部分）
   - 在 `CHANGELOG.md` 中添加变更记录
   - 如有新配置，更新 `cluster.yaml.example`

4. **手动测试**，用 Hermes Agent 验证工具端到端可用。

5. **提交**，使用清晰的 commit message：
```bash
git commit -m "feat: 新增某功能"
```

使用 conventional commits：`feat:`、`fix:`、`docs:`、`refactor:`、`test:`、`chorchore:`。

6. **推送并创建 PR**：
```bash
git push origin feature/你的功能名
```

7. 在 PR 描述中：
   - 说明改了什么、为什么改
   - 如涉及 Dashboard 变更，附上截图
   - 引用相关 issue

---

## 报告问题

使用 GitHub issue 模板：
- **Bug Report** — 报告 bug 和异常行为
- **Feature Request** — 提出新功能和改进建议

---

## 许可证

贡献即表示您同意您的贡献将在 MIT 许可证下授权。
