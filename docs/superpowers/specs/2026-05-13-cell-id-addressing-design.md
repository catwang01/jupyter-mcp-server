# Cell ID Addressing Support

**Date**: 2026-05-13
**Branch**: concurrent-notebook-support
**Status**: Design approved, pending implementation

## Problem

All cell-locating tools currently use `cell_index` (0-based integer) to identify cells. This is fragile: inserting or deleting a cell shifts all subsequent indices, making multi-step operations error-prone for LLM agents. nbformat 4.5 introduced stable `cell.id` fields (8-char hex), and a `pre_save_hook` already ensures all notebooks have cell IDs.

## Design

Add an optional `cell_id` parameter to **all** cell-locating tools. When provided, the tool resolves the ID to an index before executing. The `cell_index` parameter remains available as a fallback.

### Resolution Logic

A shared utility function in `utils.py`:

```python
def resolve_cell_index(cells, cell_id=None, cell_index=None) -> int:
```

- If `cell_id` is provided: search `cells` for a cell whose `.id` matches, return its index. Raise `ValueError` if not found.
- If only `cell_index` is provided: return it directly.
- If both are provided: `cell_id` takes priority.
- If neither is provided: raise `ValueError`.

The `cells` argument can be a list of cell objects (nbformat) or a Y.js document cell array — the function only needs `.id` on each element.

### Tool-by-Tool Parameter Changes

#### Single-cell tools (add `cell_id: Optional[str]`)

| Tool | Existing param | New param |
|------|---------------|-----------|
| `read_cell` | `cell_index: int` | + `cell_id: Optional[str]` |
| `execute_cell` | `cell_index: int` | + `cell_id: Optional[str]` |
| `edit_cell_source` | `cell_index: int` | + `cell_id: Optional[str]` |
| `overwrite_cell_source` | `cell_index: int` | + `cell_id: Optional[str]` |

For these tools, `cell_id` resolves to the same `cell_index` semantics — "the cell to operate on."

#### Insert tools (add `cell_id: Optional[str]` + `insert_position`)

| Tool | Existing param | New params |
|------|---------------|------------|
| `insert_cell` | `cell_index: int` | + `cell_id: Optional[str]`, `insert_position: Literal["before", "after"] = "after"` |
| `insert_execute_code_cell` | `cell_index: int` | + `cell_id: Optional[str]`, `insert_position: Literal["before", "after"] = "after"` (same semantics as `insert_cell`) |

When `cell_id` is provided:
- Resolve to the index of that cell
- `insert_position="after"` (default): insert at `resolved_index + 1`
- `insert_position="before"`: insert at `resolved_index`

When only `cell_index` is provided: `insert_position` is ignored, existing behavior preserved.

#### Delete tool (add `cell_ids: Optional[list[str]]`)

| Tool | Existing param | New param |
|------|---------------|-----------|
| `delete_cell` | `cell_indices: list[int]` | + `cell_ids: Optional[list[str]]` |

When `cell_ids` is provided, each ID is resolved to an index. The two lists are merged (union), deduplicated, and sorted descending for safe deletion.

#### Move tool (add `source_cell_id`, `target_cell_id`)

| Tool | Existing params | New params |
|------|----------------|------------|
| `move_cell` | `source_index: int`, `target_index: int` | + `source_cell_id: Optional[str]`, `target_cell_id: Optional[str]` |

Each ID independently resolves to its corresponding index. Can mix: e.g., `source_cell_id="abc123"` + `target_index=5`.

### MCP Tool Description Update

Add a note to tool descriptions guiding LLMs:

> If a cell ID is available (from `read_notebook` or `read_cell` output), prefer using `cell_id` over `cell_index` for more robust cell targeting.

### Where Resolution Happens

Resolution is called **inside each tool's `execute()` method**, after the notebook/cells are loaded but before the main operation. This keeps `server.py` wrappers thin (just pass through the new params) and avoids loading the notebook twice.

### `read_notebook` / `read_cell` Output

These tools should include the `cell.id` in their output so that downstream tools can reference it. `read_notebook` already shows cell indices — add the cell ID next to each cell header. `read_cell` should include it in the cell metadata line.

## Implementation Approach

Shared utility function `resolve_cell_index()` in `utils.py` (Approach A from brainstorming).

### Files to modify

1. `jupyter_mcp_server/utils.py` — add `resolve_cell_index()`
2. `jupyter_mcp_server/server.py` — update all affected `@mcp.tool()` wrappers to accept new params
3. `jupyter_mcp_server/tools/read_cell_tool.py` — add `cell_id`, call resolver, include ID in output
4. `jupyter_mcp_server/tools/execute_cell_tool.py` — add `cell_id`, call resolver
5. `jupyter_mcp_server/tools/edit_cell_source_tool.py` — add `cell_id`, call resolver
6. `jupyter_mcp_server/tools/overwrite_cell_source_tool.py` — add `cell_id`, call resolver
7. `jupyter_mcp_server/tools/insert_cell_tool.py` — add `cell_id` + `insert_position`, call resolver
8. `jupyter_mcp_server/tools/delete_cell_tool.py` — add `cell_ids`, call resolver for each
9. `jupyter_mcp_server/tools/move_cell_tool.py` — add `source_cell_id` + `target_cell_id`, call resolver
10. `jupyter_mcp_server/tools/read_notebook_tool.py` — include cell ID in output
11. `jupyter_mcp_server/tools/jupyter_cite_prompt.py` — update prompt text if applicable

### Edge Cases

- Cell ID not found → `ValueError("Cell ID '{cell_id}' not found in notebook")`
- Duplicate cell IDs in notebook → use first match, log warning
- Notebook without cell IDs (pre-4.5) → `ValueError` with helpful message suggesting to re-save the notebook
