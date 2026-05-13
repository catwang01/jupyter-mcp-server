# Copyright (c) 2024- Datalayer, Inc.
#
# BSD 3-Clause License

"""Delete cell tool implementation."""

from typing import Any, Optional
from pathlib import Path
import nbformat
from jupyter_server_client import JupyterServerClient
from jupyter_mcp_server.tools._base import BaseTool, ServerMode
from jupyter_mcp_server.notebook_manager import NotebookManager
from jupyter_mcp_server.utils import get_notebook_model, clean_notebook_outputs, resolve_cell_index


class DeleteCellTool(BaseTool):
    """Tool to delete specific cells from a notebook."""

    def _get_cell_source(self, cell: Any) -> str:
        """Get the cell source from the cell"""
        cell_source = cell.get("source", "")
        if isinstance(cell_source, list):
            return "".join(cell_source)
        else:
            return str(cell_source)

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

    async def _delete_cell_ydoc(
        self,
        serverapp: Any,
        notebook_path: str,
        cell_indices: list[int],
        *,
        cell_ids=None,
    ) -> tuple:
        """Delete cell using YDoc (collaborative editing mode).

        Args:
            serverapp: Jupyter ServerApp instance
            notebook_path: Path to the notebook
            cell_indices: List of indices of cells to delete
            cell_ids: Optional list of stable cell IDs to delete

        Returns:
            Tuple of (deleted_cells, all_indices)
        """
        nb = await get_notebook_model(serverapp, notebook_path)
        if nb:
            all_indices = self._merge_indices(nb.as_dict()["cells"], cell_indices=cell_indices, cell_ids=cell_ids)
            if max(all_indices) >= len(nb):
                raise ValueError(
                    f"Cell index {max(all_indices)} is out of range. Notebook has {len(nb)} cells."
                )
            cells = nb.delete_many_cells(all_indices)
            return cells, all_indices
        else:
            # YDoc not available, use file operations
            return await self._delete_cell_file(notebook_path, cell_indices, cell_ids=cell_ids)

    async def _delete_cell_file(
        self,
        notebook_path: str,
        cell_indices: list[int],
        *,
        cell_ids=None,
    ) -> tuple:
        """Delete cell using file operations (non-collaborative mode).

        Args:
            notebook_path: Absolute path to the notebook
            cell_indices: List of indices of cells to delete
            cell_ids: Optional list of stable cell IDs to delete

        Returns:
            Tuple of (deleted_cells, all_indices)
        """
        # Read notebook file as version 4 for consistency
        with open(notebook_path, "r", encoding="utf-8") as f:
            notebook = nbformat.read(f, as_version=4)

        clean_notebook_outputs(notebook)

        all_indices = self._merge_indices(notebook.cells, cell_indices=cell_indices, cell_ids=cell_ids)

        if max(all_indices) >= len(notebook.cells):
            raise ValueError(
                f"Cell index {max(all_indices)} is out of range. Notebook has {len(notebook.cells)} cells."
            )

        deleted_cells = []
        for cell_index in all_indices:
            cell = notebook.cells[cell_index]
            result = {
                "index": cell_index,
                "cell_type": cell.cell_type,
                "source": self._get_cell_source(cell),
            }
            deleted_cells.append(result)

        # Delete in descending order (all_indices is already sorted descending)
        for cell_index in all_indices:
            notebook.cells.pop(cell_index)

        # Write back to file
        with open(notebook_path, "w", encoding="utf-8") as f:
            nbformat.write(notebook, f)

        return deleted_cells, all_indices

    async def _delete_cell_websocket(
        self,
        notebook_manager: NotebookManager,
        cell_indices: list[int],
        notebook_path: str = "",
        *,
        cell_ids=None,
    ) -> tuple:
        """Delete cell using WebSocket connection (MCP_SERVER mode).

        Args:
            notebook_manager: Notebook manager instance
            cell_indices: List of indices of cells to delete
            cell_ids: Optional list of stable cell IDs to delete

        Returns:
            Tuple of (deleted_cells, all_indices)
        """
        async with notebook_manager.get_notebook_connection(notebook_path) as notebook:
            all_indices = self._merge_indices(notebook.as_dict()["cells"], cell_indices=cell_indices, cell_ids=cell_ids)
            if max(all_indices) >= len(notebook):
                raise ValueError(
                    f"Cell index {max(all_indices)} is out of range. Notebook has {len(notebook)} cells."
                )

            cells = notebook.delete_many_cells(all_indices)
            return cells, all_indices

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
        cell_indices: Optional[list[int]] = None,
        cell_ids: Optional[list[str]] = None,
        include_source: bool = True,
        notebook_path: str = "",
        **kwargs
    ) -> str:
        """Execute the delete_cell tool.

        This tool supports three modes of operation:

        1. JUPYTER_SERVER mode with YDoc (collaborative):
           - Checks if notebook is open in a collaborative session
           - Uses YDoc for real-time collaborative editing
           - Changes are immediately visible to all connected users

        2. JUPYTER_SERVER mode without YDoc (file-based):
           - Falls back to direct file operations using nbformat
           - Suitable when notebook is not actively being edited

        3. MCP_SERVER mode (WebSocket):
           - Uses WebSocket connection to remote Jupyter server
           - Accesses YDoc through NbModelClient

        Args:
            mode: Server mode (MCP_SERVER or JUPYTER_SERVER)
            server_client: HTTP client for MCP_SERVER mode
            contents_manager: Direct API access for JUPYTER_SERVER mode
            notebook_manager: Notebook manager instance
            cell_indices: List of cell indices to delete (0-based)
            cell_ids: List of stable cell IDs to delete
            **kwargs: Additional parameters

        Returns:
            Success message
        """
        if mode == ServerMode.JUPYTER_SERVER and contents_manager is not None:
            # JUPYTER_SERVER mode: Try YDoc first, fall back to file operations
            from jupyter_mcp_server.jupyter_extension.context import get_server_context

            context = get_server_context()
            serverapp = context.serverapp

            # Resolve to absolute path
            if serverapp and not Path(notebook_path).is_absolute():
                root_dir = serverapp.root_dir
                notebook_path = str(Path(root_dir) / notebook_path)

            if serverapp:
                # Try YDoc approach first
                cells, all_indices = await self._delete_cell_ydoc(serverapp, notebook_path, cell_indices, cell_ids=cell_ids)
            else:
                # Fall back to file operations
                cells, all_indices = await self._delete_cell_file(notebook_path, cell_indices, cell_ids=cell_ids)

        elif mode == ServerMode.MCP_SERVER and notebook_manager is not None:
            # MCP_SERVER mode: Use WebSocket connection
            cells, all_indices = await self._delete_cell_websocket(notebook_manager, cell_indices, notebook_path=notebook_path, cell_ids=cell_ids)
        else:
            raise ValueError(f"Invalid mode or missing required clients: mode={mode}")

        info_list = []
        for cell_index, cell_info in zip(all_indices, cells):
            info_list.append(f"Cell {cell_index} ({cell_info['cell_type']}) deleted successfully.")
            if include_source:
                info_list.append(f"deleted cell source:\n{cell_info['source']}")
                info_list.append("\n---\n")

        return "\n".join(info_list)
