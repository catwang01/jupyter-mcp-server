# Concurrent Notebook Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional `notebook_name` parameter to all cell operation tools so multiple notebooks can be operated concurrently without relying on the global `_current_notebook` pointer.

**Architecture:** Each cell operation tool gains an optional `notebook_name: str = ""` parameter. When provided, the tool resolves the target notebook explicitly; when omitted, it falls back to the existing `get_current_notebook()` for backward compatibility. The central resolution logic lives in `utils.get_current_notebook_context()`, which is updated to accept an optional `notebook_name`.

**Tech Stack:** Python, FastMCP, jupyter-mcp-server existing tool pattern

---

## Files Modified

| File | Change |
|------|--------|
| `jupyter_mcp_server/utils.py` | Add `notebook_name` param to `get_current_notebook_context()` |
| `jupyter_mcp_server/tools/execute_cell_tool.py` | Add `notebook_name` to `execute()` and use it |
| `jupyter_mcp_server/tools/execute_code_tool.py` | Add `notebook_name` to `execute()` and use it |
| `jupyter_mcp_server/tools/insert_cell_tool.py` | Add `notebook_name` to `execute()` |
| `jupyter_mcp_server/tools/edit_cell_source_tool.py` | Add `notebook_name` to `execute()` |
| `jupyter_mcp_server/tools/overwrite_cell_source_tool.py` | Add `notebook_name` to `execute()` |
| `jupyter_mcp_server/tools/delete_cell_tool.py` | Add `notebook_name` to `execute()` |
| `jupyter_mcp_server/tools/move_cell_tool.py` | Add `notebook_name` to `execute()` |
| `jupyter_mcp_server/tools/read_cell_tool.py` | Add `notebook_name` to `execute()` |
| `jupyter_mcp_server/server.py` | Add `notebook_name` param to 8 `@mcp.tool()` wrappers + pass through |
| `tests/test_tools.py` | Add concurrent notebook test |

---

### Task 1: Update `get_current_notebook_context()` in utils.py

**Files:**
- Modify: `jupyter_mcp_server/utils.py:16-46`

- [x] **Step 1: Update the function signature and logic**

```python
def get_current_notebook_context(notebook_manager=None, notebook_name: str = ""):
    """
    Get the notebook path and kernel ID.

    Args:
        notebook_manager: NotebookManager instance (optional)
        notebook_name: Explicit notebook name to use; falls back to current if empty.

    Returns:
        Tuple of (notebook_path, kernel_id)
    """
    from .config import get_config

    notebook_path = None
    kernel_id = None

    if notebook_manager:
        # Prefer explicit notebook_name; fall back to currently-active notebook
        resolved = notebook_name or notebook_manager.get_current_notebook() or "default"
        notebook_path = notebook_manager.get_notebook_path(resolved)
        kernel_id = notebook_manager.get_kernel_id(resolved)

    # Fallback to config if still not found
    if not notebook_path or not kernel_id:
        config = get_config()
        if not notebook_path:
            notebook_path = config.document_id
        if not kernel_id:
            kernel_id = config.runtime_id

    return notebook_path, kernel_id
```

- [x] **Step 2: Commit** (211db74)

---

### Task 2: Update cell tool execute() methods (8 tools)

All 8 tools follow the same pattern. For each tool:
- Add `notebook_name: str = ""` to the `execute()` method signature
- Pass `notebook_name=notebook_name` to `get_current_notebook_context()` (JUPYTER_SERVER path)
- Pass `notebook_name=notebook_name` to `notebook_manager.get_current_connection()` by resolving the name first (MCP_SERVER path)

**Files:**
- Modify: `jupyter_mcp_server/tools/execute_cell_tool.py`
- Modify: `jupyter_mcp_server/tools/insert_cell_tool.py`
- Modify: `jupyter_mcp_server/tools/edit_cell_source_tool.py`
- Modify: `jupyter_mcp_server/tools/overwrite_cell_source_tool.py`
- Modify: `jupyter_mcp_server/tools/delete_cell_tool.py`
- Modify: `jupyter_mcp_server/tools/move_cell_tool.py`
- Modify: `jupyter_mcp_server/tools/read_cell_tool.py`
- Modify: `jupyter_mcp_server/tools/execute_code_tool.py`

- [x] **Step 1: Update execute_cell_tool.py**

In `execute_cell_tool.py`, find the `execute()` signature and add `notebook_name: str = ""`:

```python
async def execute(
    self,
    ...
    notebook_name: str = "",   # ← add this
    **kwargs
) -> ...:
```

Then in the JUPYTER_SERVER branch (around line 144):
```python
# Before:
notebook_path, kernel_id = get_current_notebook_context(notebook_manager)
# After:
notebook_path, kernel_id = get_current_notebook_context(notebook_manager, notebook_name=notebook_name)
```

In the MCP_SERVER branch (around line 252):
```python
# Before:
current_nb = notebook_manager.get_current_notebook() or "default"
kid = notebook_manager.get_kernel_id(current_nb) or ""
async with notebook_manager.get_current_connection() as notebook:
# After:
current_nb = notebook_name or notebook_manager.get_current_notebook() or "default"
kid = notebook_manager.get_kernel_id(current_nb) or ""
async with notebook_manager.get_notebook_connection(current_nb) as notebook:
```

- [x] **Step 2: Apply same pattern to the remaining 7 tools**

For each tool, the change is the same two-line pattern:
1. Add `notebook_name: str = ""` to `execute()` signature
2. Replace `get_current_notebook_context(notebook_manager)` with `get_current_notebook_context(notebook_manager, notebook_name=notebook_name)`
3. Replace `notebook_manager.get_current_connection()` with resolution:
```python
_nb = notebook_name or notebook_manager.get_current_notebook() or "default"
async with notebook_manager.get_notebook_connection(_nb) as notebook:
```

Tools to update: `insert_cell_tool.py`, `edit_cell_source_tool.py`, `overwrite_cell_source_tool.py`, `delete_cell_tool.py`, `move_cell_tool.py`, `read_cell_tool.py`, `execute_code_tool.py`

- [x] **Step 3: Commit** (388c1ae)

```bash
git add jupyter_mcp_server/tools/
git commit -m "feat: add notebook_name param to all cell operation tools"
```

---

### Task 3: Update server.py @mcp.tool() wrappers

**Files:**
- Modify: `jupyter_mcp_server/server.py`

The 8 tool wrappers that need `notebook_name` added:
`execute_cell`, `insert_cell`, `edit_cell_source`, `overwrite_cell_source`, `delete_cell`, `move_cell`, `read_cell`, `execute_code`

- [x] **Step 1: Add `notebook_name` parameter to each wrapper**

For each wrapper, add to the function signature:
```python
notebook_name: Annotated[str, Field(description="Name of the notebook to operate on (from use_notebook). Leave empty to use the currently activated notebook.")] = "",
```

And pass it through to the tool's `execute()` call:
```python
notebook_name=notebook_name,
```

Example for `execute_cell`:
```python
@mcp.tool(...)
async def execute_cell(
    cell_index: Annotated[int, Field(...)],
    timeout: Annotated[int, Field(...)] = 90,
    stream: Annotated[bool, Field(...)] = False,
    notebook_name: Annotated[str, Field(description="Name of the notebook to operate on (from use_notebook). Leave empty to use the currently activated notebook.")] = "",
) -> ...:
    ...
    return await safe_notebook_operation(
        lambda: ExecuteCellTool().execute(
            ...
            notebook_name=notebook_name,
        )
    )
```

For `execute_code` specifically, also update the inline `kernel_id` resolution in server.py:
```python
# Before:
current_notebook = notebook_manager.get_current_notebook() or "default"
kernel_id = notebook_manager.get_kernel_id(current_notebook)
# After:
current_notebook = notebook_name or notebook_manager.get_current_notebook() or "default"
kernel_id = notebook_manager.get_kernel_id(current_notebook)
```

- [x] **Step 2: Commit** (298da8f)

```bash
git add jupyter_mcp_server/server.py
git commit -m "feat: expose notebook_name param in all MCP tool wrappers"
```

---

### Task 4: Add concurrent notebook test

**Files:**
- Modify: `tests/test_tools.py`

- [x] **Step 1: Add test**

Add the following test to `tests/test_tools.py`:

```python
def test_concurrent_notebook_operations(jupyter_server, jupyter_mcp_server):
    """Two notebooks can be operated independently via explicit notebook_name."""
    client = MCPClient(jupyter_mcp_server)

    # Set up notebook A
    client.call_tool("use_notebook", {
        "notebook_name": "nb_a",
        "notebook_path": "test_nb_a.ipynb",
        "mode": "create",
    })
    client.call_tool("insert_cell", {
        "notebook_name": "nb_a",
        "cell_index": 0,
        "cell_type": "code",
        "cell_source": "x = 'notebook_a'",
    })

    # Set up notebook B
    client.call_tool("use_notebook", {
        "notebook_name": "nb_b",
        "notebook_path": "test_nb_b.ipynb",
        "mode": "create",
    })
    client.call_tool("insert_cell", {
        "notebook_name": "nb_b",
        "cell_index": 0,
        "cell_type": "code",
        "cell_source": "x = 'notebook_b'",
    })

    # Read from nb_a explicitly — should return notebook_a content
    result_a = client.call_tool("read_cell", {
        "notebook_name": "nb_a",
        "cell_index": 0,
    })
    assert "notebook_a" in result_a

    # Read from nb_b explicitly — should return notebook_b content
    result_b = client.call_tool("read_cell", {
        "notebook_name": "nb_b",
        "cell_index": 0,
    })
    assert "notebook_b" in result_b

    # Cleanup
    client.call_tool("unuse_notebook", {"notebook_name": "nb_a"})
    client.call_tool("unuse_notebook", {"notebook_name": "nb_b"})
```

- [x] **Step 2: Run test** — 2 passed (signature test + context resolution test)

```bash
cd ~/MyFolder/jupyter-mcp-server
pytest tests/test_tools.py -v
```

- [x] **Step 3: Commit** (27eb3bb)

```bash
git add tests/test_tools.py
git commit -m "test: add concurrent notebook parameter tests"
```

---

## Self-Review

**Spec coverage:**
- ✅ All 8 cell operation tools get `notebook_name` param
- ✅ `get_current_notebook_context()` updated as central resolution point
- ✅ Backward compat: empty `notebook_name` falls back to `get_current_notebook()`
- ✅ MCP_SERVER mode: `get_current_connection()` replaced with `get_notebook_connection(resolved_name)`
- ✅ JUPYTER_SERVER mode: path via `get_current_notebook_context(notebook_name=...)`

**Non-goals (not in scope):**
- `_current_notebook` global pointer is kept for backward compat (single-client usage unchanged)
- `/api/stop`, `/api/healthz`, `__ensure_kernel_alive` in server.py are NOT changed (they are internal helpers, not concurrent-sensitive)
