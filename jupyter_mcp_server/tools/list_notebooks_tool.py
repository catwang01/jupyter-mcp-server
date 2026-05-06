# Copyright (c) 2024- Datalayer, Inc.
#
# BSD 3-Clause License

"""List notebooks and their kernel attachment status."""

from typing import Any, Optional
from jupyter_server_client import JupyterServerClient

from jupyter_mcp_server.tools._base import BaseTool, ServerMode
from jupyter_mcp_server.notebook_manager import NotebookManager
from jupyter_mcp_server.utils import format_TSV


class ListNotebooksTool(BaseTool):
    """List all notebooks with their attached kernel IDs."""

    async def execute(
        self,
        mode: ServerMode,
        notebook_manager: Optional[NotebookManager] = None,
        server_client: Optional[JupyterServerClient] = None,
        **kwargs
    ) -> str:
        """Return the current notebook → kernel attachment status.

        Args:
            mode: Server mode (MCP_SERVER or JUPYTER_SERVER)
            notebook_manager: Notebook/kernel attachment registry
            **kwargs: Additional parameters (unused)

        Returns:
            Tab-separated table with columns: Notebook_Path, Kernel_ID
        """
        if notebook_manager is None:
            return "No notebook manager available."

        attachments = notebook_manager.list_attachments()

        if not attachments:
            return "No notebooks are currently attached to a kernel."

        headers = ["Notebook_Path", "Kernel_ID"]
        rows = [[path, kernel_id] for path, kernel_id in attachments.items()]
        return format_TSV(headers, rows)
