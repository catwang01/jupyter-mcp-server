# Cell ID Addressing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional `cell_id` parameter to all cell-locating tools so agents can address cells by stable ID (nbformat 4.5 `cell.id`) instead of fragile position indices.

**Architecture:** A shared `resolve_cell_index(cells, *, cell_id, cell_index)` utility in `utils.py` converts `cell_id → index` at the start of each backend method. Tools keep their existing `cell_index` path; `cell_id` takes priority when provided. `read_notebook` and `read_cell` output is updated to surface cell IDs so agents can discover them.

**Tech Stack:** Python 3.10, nbformat 4.5, pytest (unit tests only — no server needed for core logic)

**Spec:** `docs/superpowers/specs/2026-05-13-cell-id-addressing-design.md`

---

## File Map

| File | Change |
|------|--------|
| `jupyter_mcp_server/utils.py` | Add `resolve_cell_index()` |
| `jupyter_mcp_server/models.py` | Add `ID` column to `Notebook.format_output()` brief mode |
| `jupyter_mcp_server/tools/read_cell_tool.py` | Add `cell_id` param; include ID in output header |
| `jupyter_mcp_server/tools/edit_cell_source_tool.py` | Add `cell_id`; resolve in each backend |
| `jupyter_mcp_server/tools/overwrite_cell_source_tool.py` | Add `cell_id`; resolve in each backend |
| `jupyter_mcp_server/tools/execute_cell_tool.py` | Add `cell_id`; resolve in each code path; rename internal `cell_id` var |
| `jupyter_mcp_server/tools/insert_cell_tool.py` | Add `cell_id` + `insert_position`; resolve in each backend |
| `jupyter_mcp_server/tools/delete_cell_tool.py` | Add `cell_ids`; resolve in each backend |
| `jupyter_mcp_server/tools/move_cell_tool.py` | Add `source_cell_id`, `target_cell_id`; resolve in each backend |
| `jupyter_mcp_server/server.py` | Update all `@mcp.tool()` wrappers |
| `jupyter_mcp_server/tools/jupyter_cite_prompt.py` | Include cell ID in cited cell headers |
| `tests/test_resolve_cell_index.py` | New: unit tests for `resolve_cell_index` |

---

## Task 1: Core utility `resolve_cell_index()` in `utils.py`

**Files:**
- Modify: `jupyter_mcp_server/utils.py` (append after the last function in the utils section, before the execution helpers section at line ~416)
- Create: `tests/test_resolve_cell_index.py`

- [ ] **Step 1.1: Write the failing tests**

Create `tests/test_resolve_cell_index.py`:

```python
"""Unit tests for resolve_cell_index utility."""

import pytest
from jupyter_mcp_server.utils import resolve_cell_index


class TestResolveCellIndex:

    def test_resolve_by_cell_id_dict(self):
        cells = [{"id": "abc123"}, {"id": "def456"}, {"id": "ghi789"}]
        assert resolve_cell_index(cells, cell_id="def456") == 1

    def test_resolve_by_cell_id_object(self):
        from types import SimpleNamespace
        cells = [SimpleNamespace(id="abc"), SimpleNamespace(id="xyz")]
        assert resolve_cell_index(cells, cell_id="xyz") == 1

    def test_resolve_by_cell_index(self):
        cells = [{"id": "abc123"}, {"id": "def456"}]
        assert resolve_cell_index(cells, cell_index=0) == 0
        assert resolve_cell_index(cells, cell_index=1) == 1

    def test_cell_id_priority_over_index(self):
        cells = [{"id": "abc123"}, {"id": "def456"}]
        # cell_id="def456" is at index 1, but cell_index=0 is given — cell_id wins
        assert resolve_cell_index(cells, cell_id="def456", cell_index=0) == 1

    def test_cell_id_not_found_raises(self):
        cells = [{"id": "abc123"}]
        with pytest.raises(ValueError, match="not found"):
            resolve_cell_index(cells, cell_id="zzz999")

    def test_neither_provided_raises(self):
        cells = [{"id": "abc123"}]
        with pytest.raises(ValueError, match="Either"):
            resolve_cell_index(cells)

    def test_both_none_raises(self):
        cells = [{"id": "abc123"}]
        with pytest.raises(ValueError, match="Either"):
            resolve_cell_index(cells, cell_id=None, cell_index=None)

    def test_empty_cells_with_cell_id_raises(self):
        with pytest.raises(ValueError, match="not found"):
            resolve_cell_index([], cell_id="abc")

    def test_first_match_returned_on_duplicate_ids(self):
        cells = [{"id": "dup"}, {"id": "dup"}]
        assert resolve_cell_index(cells, cell_id="dup") == 0

    def test_get_method_used_for_dict_cells(self):
        # Cells without "id" key — should not match
        cells = [{"source": "print()"}, {"id": "real-id"}]
        assert resolve_cell_index(cells, cell_id="real-id") == 1
```

- [ ] **Step 1.2: Run tests to verify they fail**

```bash
cd /Users/tanhuan/jupyter-mcp-server
pytest tests/test_resolve_cell_index.py -v
```

Expected: `ImportError` or `AttributeError` because `resolve_cell_index` doesn't exist yet.

- [ ] **Step 1.3: Implement `resolve_cell_index` in `utils.py`**

Add this function before the `###` comment block at line ~416 (just before the execution helpers section):

```python
def resolve_cell_index(cells, *, cell_id: str = None, cell_index: int = None) -> int:
    """Resolve cell_id or cell_index to a concrete integer index.

    Priority: cell_id > cell_index.
    Works with dict-like cells (Y.js, WebSocket) and object cells (nbformat).
    """
    if cell_id is not None:
        for i, cell in enumerate(cells):
            cid = cell.get("id") if hasattr(cell, "get") else getattr(cell, "id", None)
            if cid == cell_id:
                return i
        raise ValueError(f"Cell with id '{cell_id}' not found in notebook")
    if cell_index is not None:
        return cell_index
    raise ValueError("Either cell_id or cell_index must be provided")
```

- [ ] **Step 1.4: Run tests to verify they pass**

```bash
pytest tests/test_resolve_cell_index.py -v
```

Expected: all 10 tests PASS.

- [ ] **Step 1.5: Commit**

```bash
git add jupyter_mcp_server/utils.py tests/test_resolve_cell_index.py
git commit -m "feat: add resolve_cell_index() utility for cell ID addressing"
```

---

## Task 2: Surface cell IDs in `read_notebook` and `read_cell` output

**Files:**
- Modify: `jupyter_mcp_server/models.py` (brief format in `Notebook.format_output()`)
- Modify: `jupyter_mcp_server/tools/read_cell_tool.py` (output header line)

- [ ] **Step 2.1: Add `ID` column to `Notebook.format_output()` brief mode**

In `models.py`, in the `format_output()` method around line 98, replace the headers and row construction:

Old:
```python
            headers = ["Index", "Type", "Count", "First Line"]
            rows = []

            for idx, cell in enumerate(cells_to_show):
                absolute_idx = start_index + idx
                cell_type = cell.cell_type
                execution_count = cell.execution_count if cell.execution_count else 'N/A'
                overview = cell.get_overview()

                rows.append([absolute_idx, cell_type, execution_count, overview])
```

New:
```python
            headers = ["Index", "ID", "Type", "Count", "First Line"]
            rows = []

            for idx, cell in enumerate(cells_to_show):
                absolute_idx = start_index + idx
                cell_type = cell.cell_type
                execution_count = cell.execution_count if cell.execution_count else 'N/A'
                overview = cell.get_overview()

                rows.append([absolute_idx, cell.id or "", cell_type, execution_count, overview])
```

- [ ] **Step 2.2: Add cell ID to `read_cell` output header**

In `read_cell_tool.py`, replace the info_list header line (around line 70):

Old:
```python
        info_list.append(f"=====Cell {cell_index} | type: {cell.cell_type} | execution count: {cell.execution_count if cell.execution_count else 'N/A'}=====")
```

New:
```python
        info_list.append(f"=====Cell {cell_index} | id: {cell.id or 'N/A'} | type: {cell.cell_type} | execution count: {cell.execution_count if cell.execution_count else 'N/A'}=====")
```

- [ ] **Step 2.3: Commit**

```bash
git add jupyter_mcp_server/models.py jupyter_mcp_server/tools/read_cell_tool.py
git commit -m "feat: include cell ID in read_notebook brief output and read_cell header"
```

---

## Task 3: `read_cell_tool.py` — add `cell_id` parameter

**Files:**
- Modify: `jupyter_mcp_server/tools/read_cell_tool.py`
- Modify: `jupyter_mcp_server/server.py` (the `read_cell` wrapper)

- [ ] **Step 3.1: Update `ReadCellTool.execute()` signature and add resolution**

In `read_cell_tool.py`, add the import at the top of the file:
```python
from jupyter_mcp_server.utils import resolve_cell_index
```

Update the `execute()` method signature (add `cell_id` parameter):
```python
    async def execute(
        self,
        mode: ServerMode,
        server_client: Optional[JupyterServerClient] = None,
        kernel_client: Optional[Any] = None,
        contents_manager: Optional[Any] = None,
        kernel_manager: Optional[Any] = None,
        kernel_spec_manager: Optional[Any] = None,
        notebook_manager: Optional[NotebookManager] = None,
        # Tool-specific parameters
        cell_index: int = None,
        cell_id: Optional[str] = None,
        include_outputs: bool = True,
        notebook_path: str = "",
        **kwargs
    ) -> list[str | ImageContent]:
```

After loading the notebook (the `if`/`elif` block), and before the index check, add resolution:

Replace:
```python
        if cell_index >= len(notebook):
            return f"Cell index {cell_index} is out of range. Notebook has {len(notebook)} cells."
        cell = notebook[cell_index]
```

With:
```python
        cell_index = resolve_cell_index(notebook.cells, cell_id=cell_id, cell_index=cell_index)
        if cell_index >= len(notebook):
            return f"Cell index {cell_index} is out of range. Notebook has {len(notebook)} cells."
        cell = notebook[cell_index]
```

- [ ] **Step 3.2: Update `read_cell` wrapper in `server.py`**

Find the `async def read_cell(` function. Replace:
```python
async def read_cell(
    notebook_path: Annotated[str, Field(description="Path to the notebook file, relative to the Jupyter server root")],
    cell_index: Annotated[int, Field(description="Index of the cell to read (0-based)", ge=0)],
    include_outputs: Annotated[bool, Field(description="Include outputs in the response (only for code cells)")] = True,
) -> Annotated[list[str | ImageContent], Field(description="Cell information including index, type, source, and outputs")]:
    """Read a specific cell from a notebook."""
    return await safe_notebook_operation(
        lambda: ReadCellTool().execute(
            mode=server_context.mode,
            contents_manager=server_context.contents_manager,
            kernel_manager=server_context.kernel_manager,
            notebook_manager=notebook_manager,
            notebook_path=notebook_path,
            cell_index=cell_index,
            include_outputs=include_outputs,
        )
    )
```

With:
```python
async def read_cell(
    notebook_path: Annotated[str, Field(description="Path to the notebook file, relative to the Jupyter server root")],
    cell_index: Annotated[Optional[int], Field(description="Index of the cell to read (0-based). Required if cell_id not provided.", ge=0)] = None,
    cell_id: Annotated[Optional[str], Field(description="Stable cell ID (nbformat 4.5+). Prefer over cell_index when available.")] = None,
    include_outputs: Annotated[bool, Field(description="Include outputs in the response (only for code cells)")] = True,
) -> Annotated[list[str | ImageContent], Field(description="Cell information including index, type, source, and outputs")]:
    """Read a specific cell from a notebook.

    Prefer cell_id when available (shown in read_notebook output). Fall back to cell_index (0-based) when cell IDs are not available.
    """
    return await safe_notebook_operation(
        lambda: ReadCellTool().execute(
            mode=server_context.mode,
            contents_manager=server_context.contents_manager,
            kernel_manager=server_context.kernel_manager,
            notebook_manager=notebook_manager,
            notebook_path=notebook_path,
            cell_index=cell_index,
            cell_id=cell_id,
            include_outputs=include_outputs,
        )
    )
```

Also add `Optional` to the `from typing import` line in `server.py` if not already there.

- [ ] **Step 3.3: Commit**

```bash
git add jupyter_mcp_server/tools/read_cell_tool.py jupyter_mcp_server/server.py
git commit -m "feat: add cell_id param to read_cell tool"
```

---

## Task 4: `edit_cell_source_tool.py` — add `cell_id` parameter

**Files:**
- Modify: `jupyter_mcp_server/tools/edit_cell_source_tool.py`
- Modify: `jupyter_mcp_server/server.py` (the `edit_cell_source` wrapper)

- [ ] **Step 4.1: Add import and update backend methods**

Add to imports in `edit_cell_source_tool.py`:
```python
from jupyter_mcp_server.utils import resolve_cell_index
```

Update `_edit_cell_ydoc` signature and add resolution after loading `nb`:
```python
    async def _edit_cell_ydoc(
        self, serverapp: Any, notebook_path: str,
        cell_index: int, old_string: str, new_string: str, replace_all: bool,
        *, cell_id: str = None,
    ) -> str:
        nb = await get_notebook_model(serverapp, notebook_path)

        if nb:
            cell_index = resolve_cell_index(nb.as_dict()["cells"], cell_id=cell_id, cell_index=cell_index)
            if cell_index >= len(nb):
                # ... rest unchanged
```

Update `_edit_cell_file` signature and add resolution after reading notebook:
```python
    async def _edit_cell_file(
        self, notebook_path: str, cell_index: int,
        old_string: str, new_string: str, replace_all: bool,
        *, cell_id: str = None,
    ) -> str:
        with open(notebook_path, "r", encoding="utf-8") as f:
            notebook = nbformat.read(f, as_version=4)
        clean_notebook_outputs(notebook)
        cell_index = resolve_cell_index(notebook.cells, cell_id=cell_id, cell_index=cell_index)
        # ... rest unchanged
```

Update `_edit_cell_websocket` signature and add resolution inside the context manager:
```python
    async def _edit_cell_websocket(
        self, notebook_manager: NotebookManager, cell_index: int,
        old_string: str, new_string: str, replace_all: bool,
        notebook_path: str = "", *, cell_id: str = None,
    ) -> str:
        async with notebook_manager.get_notebook_connection(notebook_path) as notebook:
            cell_index = resolve_cell_index(notebook.as_dict()["cells"], cell_id=cell_id, cell_index=cell_index)
            if cell_index >= len(notebook):
                # ... rest unchanged
```

- [ ] **Step 4.2: Update `execute()` signature and pass `cell_id` to backends**

Add `cell_id: Optional[str] = None` to the `execute()` method params (after `cell_index`).

In the three dispatch branches, pass `cell_id=cell_id` to each backend call:
```python
            if serverapp:
                diff = await self._edit_cell_ydoc(
                    serverapp, notebook_path, cell_index,
                    old_string, new_string, replace_all, cell_id=cell_id,
                )
            else:
                diff = await self._edit_cell_file(
                    notebook_path, cell_index,
                    old_string, new_string, replace_all, cell_id=cell_id,
                )
        elif mode == ServerMode.MCP_SERVER and notebook_manager is not None:
            diff = await self._edit_cell_websocket(
                notebook_manager, cell_index,
                old_string, new_string, replace_all,
                notebook_path=notebook_path, cell_id=cell_id,
            )
```

- [ ] **Step 4.3: Update `edit_cell_source` wrapper in `server.py`**

Find `async def edit_cell_source(`. Replace the `cell_index` param declaration and add `cell_id`:
```python
async def edit_cell_source(
    notebook_path: Annotated[str, Field(description="Path to the notebook file, relative to the Jupyter server root")],
    cell_index: Annotated[Optional[int], Field(description="Index of the cell to edit (0-based). Required if cell_id not provided.", ge=0)] = None,
    cell_id: Annotated[Optional[str], Field(description="Stable cell ID (nbformat 4.5+). Prefer over cell_index when available.")] = None,
    old_string: Annotated[str, Field(description="Exact string to find in cell source")] = None,
    new_string: Annotated[str, Field(description="Replacement string")] = None,
    replace_all: Annotated[bool, Field(description="Replace all occurrences (default: first only)")] = False,
    ...
```

Add `cell_id=cell_id` to the `EditCellSourceTool().execute(...)` call.

Update the docstring:
```python
    """Surgical find-and-replace within a cell's source. Prefer cell_id over cell_index when available."""
```

- [ ] **Step 4.4: Run unit tests**

```bash
pytest tests/test_edit_cell_source.py::TestEditCellSourceValidation tests/test_edit_cell_source.py::TestEditCellSourceApply -v
```

Expected: all pass (no regression in existing logic).

- [ ] **Step 4.5: Commit**

```bash
git add jupyter_mcp_server/tools/edit_cell_source_tool.py jupyter_mcp_server/server.py
git commit -m "feat: add cell_id param to edit_cell_source tool"
```

---

## Task 5: `overwrite_cell_source_tool.py` — add `cell_id` parameter

**Files:**
- Modify: `jupyter_mcp_server/tools/overwrite_cell_source_tool.py`
- Modify: `jupyter_mcp_server/server.py` (the `overwrite_cell_source` wrapper)

- [ ] **Step 5.1: Add import and update backend methods**

Add to imports:
```python
from jupyter_mcp_server.utils import resolve_cell_index
```

Update `_overwrite_cell_ydoc` — add `*, cell_id: str = None` to signature, add resolution after `nb = await get_notebook_model(...)`:
```python
    async def _overwrite_cell_ydoc(self, serverapp, notebook_path, cell_index, cell_source, *, cell_id=None):
        nb = await get_notebook_model(serverapp, notebook_path)
        if nb:
            cell_index = resolve_cell_index(nb.as_dict()["cells"], cell_id=cell_id, cell_index=cell_index)
            # ... rest unchanged
```

Update `_overwrite_cell_file` — add `*, cell_id: str = None`, add resolution after `nbformat.read(...)`:
```python
    async def _overwrite_cell_file(self, notebook_path, cell_index, cell_source, *, cell_id=None):
        with open(notebook_path, "r", encoding="utf-8") as f:
            notebook = nbformat.read(f, as_version=4)
        clean_notebook_outputs(notebook)
        cell_index = resolve_cell_index(notebook.cells, cell_id=cell_id, cell_index=cell_index)
        # ... rest unchanged
```

Update `_overwrite_cell_websocket` — add `*, cell_id: str = None`, add resolution inside context manager:
```python
    async def _overwrite_cell_websocket(self, notebook_manager, cell_index, cell_source, notebook_path="", *, cell_id=None):
        async with notebook_manager.get_notebook_connection(notebook_path) as notebook:
            cell_index = resolve_cell_index(notebook.as_dict()["cells"], cell_id=cell_id, cell_index=cell_index)
            # ... rest unchanged
```

- [ ] **Step 5.2: Update `execute()` signature and pass `cell_id` to backends**

Add `cell_id: Optional[str] = None` after `cell_index` in `execute()`. Pass `cell_id=cell_id` to each backend call (same pattern as Task 4).

- [ ] **Step 5.3: Update `overwrite_cell_source` wrapper in `server.py`**

Same pattern as Task 4 — make `cell_index` optional, add `cell_id`, update docstring, pass to execute.

- [ ] **Step 5.4: Commit**

```bash
git add jupyter_mcp_server/tools/overwrite_cell_source_tool.py jupyter_mcp_server/server.py
git commit -m "feat: add cell_id param to overwrite_cell_source tool"
```

---

## Task 6: `execute_cell_tool.py` — add `cell_id` parameter

**Files:**
- Modify: `jupyter_mcp_server/tools/execute_cell_tool.py`
- Modify: `jupyter_mcp_server/server.py` (the `execute_cell` and `insert_execute_code_cell` wrappers)

**Note:** The tool currently has a local variable named `cell_id` (line 176: `cell_id = ydoc.ycells[cell_index].get("id")`). Rename it to `ydoc_cell_id` throughout the method to avoid shadowing the new parameter.

- [ ] **Step 6.1: Add import**

```python
from jupyter_mcp_server.utils import resolve_cell_index
```

- [ ] **Step 6.2: Add `cell_id` to `execute()` and add resolution at each code path**

Add `cell_id: Optional[str] = None` to `execute()` params (after `cell_index`).

**Code path 1 — JUPYTER_SERVER + YDoc** (around line 168–197):

After `if ydoc:`, resolve before the range check:
```python
            if ydoc:
                cell_index = resolve_cell_index(
                    ydoc.ycells, cell_id=cell_id, cell_index=cell_index
                )
                num_cells = len(ydoc.ycells)
                if cell_index >= num_cells:
                    raise ValueError(...)

                ydoc_cell_id = ydoc.ycells[cell_index].get("id")   # renamed from cell_id
                cell_source = ydoc.ycells[cell_index].get("source")
                ...
                outputs = await execute_via_execution_stack(
                    ...
                    cell_id=ydoc_cell_id,     # renamed
                    ...
                )
```

**Code path 2 — JUPYTER_SERVER + file** (around line 200–230):

After `notebook = nbformat.read(...)`, add:
```python
                cell_index = resolve_cell_index(notebook.cells, cell_id=cell_id, cell_index=cell_index)
```

**Code path 3 — MCP_SERVER** (around line 247–):

Inside `async with notebook_manager.get_notebook_connection(...) as notebook:`, add:
```python
                cell_index = resolve_cell_index(
                    notebook.as_dict()["cells"], cell_id=cell_id, cell_index=cell_index
                )
```

- [ ] **Step 6.3: Update `execute_cell` wrapper in `server.py`**

Make `cell_index` optional, add `cell_id`. Add `cell_id=cell_id` to the execute call. Update docstring.

- [ ] **Step 6.4: Commit**

```bash
git add jupyter_mcp_server/tools/execute_cell_tool.py jupyter_mcp_server/server.py
git commit -m "feat: add cell_id param to execute_cell tool"
```

---

## Task 7: `insert_cell_tool.py` — add `cell_id` + `insert_position`

**Files:**
- Modify: `jupyter_mcp_server/tools/insert_cell_tool.py`
- Modify: `jupyter_mcp_server/server.py` (`insert_cell` and `insert_execute_code_cell` wrappers)

When `cell_id` is provided, it identifies an existing cell. `insert_position="after"` (default) inserts at `resolved_index + 1`; `"before"` inserts at `resolved_index`. When only `cell_index` is given, `insert_position` is ignored.

- [ ] **Step 7.1: Add import and a helper method**

Add to imports:
```python
from typing import Literal
from jupyter_mcp_server.utils import resolve_cell_index
```

Add a helper to `InsertCellTool`:
```python
    @staticmethod
    def _resolve_insert_index(cells, *, cell_id=None, cell_index=None, insert_position="after"):
        """Convert cell_id + insert_position to a concrete insertion index."""
        if cell_id is not None:
            resolved = resolve_cell_index(cells, cell_id=cell_id)
            return resolved + 1 if insert_position == "after" else resolved
        if cell_index is not None:
            return cell_index
        raise ValueError("Either cell_id or cell_index must be provided")
```

- [ ] **Step 7.2: Update each backend method**

Add `*, cell_id=None, insert_position="after"` to `_insert_cell_ydoc`, `_insert_cell_file`, `_insert_cell_websocket`.

In each backend, replace the `cell_index` used in `_validate_cell_insertion_params` with the resolved value. Example for `_insert_cell_ydoc`:

```python
    async def _insert_cell_ydoc(self, serverapp, notebook_path, cell_index, cell_type, cell_source,
                                 *, cell_id=None, insert_position="after"):
        nb = await get_notebook_model(serverapp, notebook_path)
        if nb:
            total_cells = len(nb)
            if cell_id is not None:
                cell_index = self._resolve_insert_index(
                    nb.as_dict()["cells"], cell_id=cell_id, insert_position=insert_position
                )
            actual_index = self._validate_cell_insertion_params(cell_index, total_cells, cell_type)
            # ... rest unchanged
```

Same pattern for `_insert_cell_file` (use `notebook.cells`) and `_insert_cell_websocket` (use `notebook.as_dict()["cells"]`).

- [ ] **Step 7.3: Update `execute()` signature**

Add `cell_id: Optional[str] = None` and `insert_position: Literal["before", "after"] = "after"` to params. Pass them to each backend call.

- [ ] **Step 7.4: Update `insert_cell` wrapper in `server.py`**

```python
async def insert_cell(
    notebook_path: Annotated[str, Field(...)],
    cell_index: Annotated[Optional[int], Field(description="Target index for insertion (0-based), use -1 to append at end. Required if cell_id not provided.", ge=-1)] = None,
    cell_id: Annotated[Optional[str], Field(description="Stable cell ID (nbformat 4.5+) of the reference cell. Use with insert_position.")] = None,
    insert_position: Annotated[Literal["before", "after"], Field(description="Insert before or after the cell identified by cell_id. Ignored when cell_index is used.")] = "after",
    cell_type: Annotated[Literal["code", "markdown"], Field(...)] = None,
    cell_source: Annotated[str, Field(...)] = None,
) -> ...:
    """Insert a cell at a specified position in the notebook.

    When cell_id is provided, insert_position controls placement relative to that cell.
    When cell_index is provided, insert_position is ignored.
    """
```

Add `cell_id=cell_id, insert_position=insert_position` to the execute call.

- [ ] **Step 7.5: Update `insert_execute_code_cell` wrapper in `server.py`**

`insert_execute_code_cell` calls `InsertCellTool` then `ExecuteCellTool`. It needs the actual insertion index for the execute call. Parse it from the insert result string:

```python
import re

@with_hooks("insert_execute_code_cell")
async def insert_execute_code_cell(
    notebook_path: Annotated[str, Field(...)],
    cell_index: Annotated[Optional[int], Field(description="Index at which to insert and execute (0-based). Required if cell_id not provided.", ge=-1)] = None,
    cell_id: Annotated[Optional[str], Field(description="Stable cell ID. Insert relative to this cell using insert_position.")] = None,
    insert_position: Annotated[Literal["before", "after"], Field(description="Insert before or after the cell identified by cell_id. Ignored when cell_index is used.")] = "after",
    cell_source: Annotated[str, Field(description="Code source for the cell")] = None,
    timeout: Annotated[int, Field(description="Maximum seconds to wait for execution")] = 90,
) -> ...:
    """Insert a code cell and immediately execute it. Requires a kernel attached via attach_kernel.

    Prefer cell_id when available. Use insert_position to control placement relative to cell_id.
    """
    insert_result = await safe_notebook_operation(
        lambda: InsertCellTool().execute(
            mode=server_context.mode,
            contents_manager=server_context.contents_manager,
            kernel_manager=server_context.kernel_manager,
            notebook_manager=notebook_manager,
            notebook_path=notebook_path,
            cell_index=cell_index,
            cell_id=cell_id,
            insert_position=insert_position,
            cell_source=cell_source,
            cell_type="code",
        )
    )

    # InsertCellTool returns "Cell inserted successfully at index N ..."
    match = re.search(r"at index (\d+)", insert_result)
    actual_index = int(match.group(1)) if match else cell_index

    return await safe_notebook_operation(
        lambda: ExecuteCellTool().execute(
            mode=server_context.mode,
            contents_manager=server_context.contents_manager,
            kernel_manager=server_context.kernel_manager,
            notebook_manager=notebook_manager,
            notebook_path=notebook_path,
            cell_index=actual_index,
            timeout_seconds=timeout,
            stream=False,
            progress_interval=0,
        )
    )
```

Add `import re` near the top of `server.py` if not already present.

- [ ] **Step 7.6: Commit**

```bash
git add jupyter_mcp_server/tools/insert_cell_tool.py jupyter_mcp_server/server.py
git commit -m "feat: add cell_id + insert_position params to insert_cell tool"
```

---

## Task 8: `delete_cell_tool.py` — add `cell_ids`

**Files:**
- Modify: `jupyter_mcp_server/tools/delete_cell_tool.py`
- Modify: `jupyter_mcp_server/server.py` (the `delete_cell` wrapper)

When `cell_ids` is provided, each ID is resolved to an index. The resolved indices are merged (union) with `cell_indices` (if also provided), deduplicated, and sorted descending before deletion.

- [ ] **Step 8.1: Add import**

```python
from jupyter_mcp_server.utils import resolve_cell_index
```

- [ ] **Step 8.2: Add a helper method to `DeleteCellTool`**

```python
    @staticmethod
    def _merge_indices(cells, cell_indices=None, cell_ids=None):
        """Resolve cell_ids to indices and merge with cell_indices (union, deduped, sorted desc)."""
        resolved = list(cell_indices) if cell_indices else []
        if cell_ids:
            for cid in cell_ids:
                idx = resolve_cell_index(cells, cell_id=cid)
                resolved.append(idx)
        if not resolved:
            raise ValueError("Either cell_indices or cell_ids must be provided")
        return sorted(set(resolved), reverse=True)
```

- [ ] **Step 8.3: Update each backend method**

Add `*, cell_ids=None` to `_delete_cell_ydoc`, `_delete_cell_file`, `_delete_cell_websocket`.

In each backend, replace the raw `cell_indices` with a merged+resolved list. Example for `_delete_cell_ydoc`:

```python
    async def _delete_cell_ydoc(self, serverapp, notebook_path, cell_indices, *, cell_ids=None):
        nb = await get_notebook_model(serverapp, notebook_path)
        if nb:
            all_indices = self._merge_indices(nb.as_dict()["cells"], cell_indices=cell_indices, cell_ids=cell_ids)
            if max(all_indices) >= len(nb):
                raise ValueError(...)
            cells = nb.delete_many_cells(all_indices)
            return cells, all_indices
```

Note: the backend now returns `(cells, all_indices)` so the `execute()` method knows the actual indices used (for the output message). **All three backends** (`_delete_cell_ydoc`, `_delete_cell_file`, `_delete_cell_websocket`) must be updated to return this tuple. For `_delete_cell_file` and `_delete_cell_websocket`, apply the same `_merge_indices` call pattern using `notebook.cells` and `notebook.as_dict()["cells"]` respectively.

- [ ] **Step 8.4: Update `execute()` signature and dispatch**

Add `cell_ids: Optional[list[str]] = None` to `execute()` params. Pass `cell_ids=cell_ids` to each backend call. Update the output loop to use the returned `all_indices`.

```python
        cells, all_indices = await self._delete_cell_ydoc(...)
        ...
        for cell_index, cell_info in zip(all_indices, cells):
            ...
```

- [ ] **Step 8.5: Update `delete_cell` wrapper in `server.py`**

```python
async def delete_cell(
    notebook_path: Annotated[str, Field(...)],
    cell_indices: Annotated[Optional[list[int]], Field(description="List of cell indices to delete (0-based). Required if cell_ids not provided.")] = None,
    cell_ids: Annotated[Optional[list[str]], Field(description="List of stable cell IDs to delete. Merged with cell_indices if both provided.")] = None,
    include_source: Annotated[bool, Field(...)] = True,
) -> ...:
    """Delete specific cells from a notebook.

    Prefer cell_ids when available. Both cell_ids and cell_indices can be provided simultaneously (union).
    """
```

Add `cell_ids=cell_ids` to the execute call.

- [ ] **Step 8.6: Commit**

```bash
git add jupyter_mcp_server/tools/delete_cell_tool.py jupyter_mcp_server/server.py
git commit -m "feat: add cell_ids param to delete_cell tool"
```

---

## Task 9: `move_cell_tool.py` — add `source_cell_id`, `target_cell_id`

**Files:**
- Modify: `jupyter_mcp_server/tools/move_cell_tool.py`
- Modify: `jupyter_mcp_server/server.py` (the `move_cell` wrapper)

- [ ] **Step 9.1: Add import**

```python
from jupyter_mcp_server.utils import resolve_cell_index
```

- [ ] **Step 9.2: Update each backend method**

Add `*, source_cell_id=None, target_cell_id=None` to `_move_cell_ydoc`, `_move_cell_file`, `_move_cell_websocket`.

In each backend, resolve the IDs after loading cells. Example for `_move_cell_ydoc`:

```python
    async def _move_cell_ydoc(self, serverapp, notebook_path, source_index, target_index,
                               *, source_cell_id=None, target_cell_id=None):
        nb = await get_notebook_model(serverapp, notebook_path)
        if nb:
            cells = nb.as_dict()["cells"]
            source_index = resolve_cell_index(cells, cell_id=source_cell_id, cell_index=source_index)
            target_index = resolve_cell_index(cells, cell_id=target_cell_id, cell_index=target_index)
            self._validate_move(source_index, target_index, len(nb))
            # ... rest unchanged
```

For `_move_cell_file` — use `notebook.cells` for resolution.

For `_move_cell_websocket` — use `notebook.as_dict()["cells"]` for resolution, inside the context manager.

- [ ] **Step 9.3: Update `execute()` signature and dispatch**

Add `source_cell_id: Optional[str] = None` and `target_cell_id: Optional[str] = None` to `execute()`. Pass them to each backend. Update the final output message to mention cell IDs if used.

- [ ] **Step 9.4: Update `move_cell` wrapper in `server.py`**

```python
async def move_cell(
    notebook_path: Annotated[str, Field(...)],
    source_index: Annotated[Optional[int], Field(description="Index of the cell to move (0-based). Required if source_cell_id not provided.", ge=0)] = None,
    target_index: Annotated[Optional[int], Field(description="Destination index (0-based). Required if target_cell_id not provided.", ge=0)] = None,
    source_cell_id: Annotated[Optional[str], Field(description="Stable cell ID of the cell to move.")] = None,
    target_cell_id: Annotated[Optional[str], Field(description="Stable cell ID of the destination position.")] = None,
) -> ...:
    """Move a cell from one position to another within a notebook.

    Prefer source_cell_id/target_cell_id when available. Can mix: e.g., source_cell_id + target_index.
    """
```

Add `source_cell_id=source_cell_id, target_cell_id=target_cell_id` to the execute call.

- [ ] **Step 9.5: Run move_cell unit tests**

```bash
pytest tests/test_move_cell.py::TestMoveCellValidation -v
```

Expected: all pass.

- [ ] **Step 9.6: Commit**

```bash
git add jupyter_mcp_server/tools/move_cell_tool.py jupyter_mcp_server/server.py
git commit -m "feat: add source_cell_id/target_cell_id params to move_cell tool"
```

---

## Task 10: Update `jupyter_cite_prompt.py` output

**Files:**
- Modify: `jupyter_mcp_server/tools/jupyter_cite_prompt.py`

The cite prompt uses string-based cell index parsing (`"0,1-3"`), which is a different interface. We do not add `cell_id` addressing here. We only update the output to include cell IDs in cited cell headers.

- [ ] **Step 10.1: Update cited cell header to include cell ID**

In `execute()`, replace the prompt header line (around line 164):

Old:
```python
            prompt_list.append(f"=====Cell {cell_index} | type: {cell.cell_type} | execution count: {cell.execution_count if cell.execution_count else 'N/A'}=====")
```

New:
```python
            prompt_list.append(f"=====Cell {cell_index} | id: {cell.id or 'N/A'} | type: {cell.cell_type} | execution count: {cell.execution_count if cell.execution_count else 'N/A'}=====")
```

- [ ] **Step 10.2: Commit**

```bash
git add jupyter_mcp_server/tools/jupyter_cite_prompt.py
git commit -m "feat: include cell ID in jupyter_cite_prompt output headers"
```

---

## Task 11: Final — run all unit tests

- [ ] **Step 11.1: Run all unit tests**

```bash
pytest tests/test_resolve_cell_index.py tests/test_edit_cell_source.py::TestEditCellSourceValidation tests/test_edit_cell_source.py::TestEditCellSourceApply tests/test_move_cell.py::TestMoveCellValidation -v
```

Expected: all pass.

- [ ] **Step 11.2: Verify server.py imports**

Ensure `Optional` is imported in `server.py`. Check with:

```bash
grep "from typing import" jupyter_mcp_server/server.py
```

If `Optional` is missing, add it to the import line.

- [ ] **Step 11.3: Final commit if needed**

```bash
git add -u
git status  # verify only intended files changed
git commit -m "feat: complete cell_id addressing for all cell-locating tools"
```
