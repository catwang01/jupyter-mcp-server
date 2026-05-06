# Copyright (c) 2024- Datalayer, Inc.
#
# BSD 3-Clause License

"""Detach kernel tool implementation."""

import logging
from typing import Any, Optional

from jupyter_mcp_server.tools._base import BaseTool, ServerMode
from jupyter_mcp_server.notebook_manager import NotebookManager

logger = logging.getLogger(__name__)


class DetachKernelTool(BaseTool):
    """Remove the association between a notebook path and its kernel."""

    async def execute(
        self,
        mode: ServerMode,
        notebook_manager: Optional[NotebookManager] = None,
        notebook_path: str = "",
        **kwargs,
    ) -> str:
        if not notebook_path:
            raise ValueError("notebook_path is required")

        kernel_id = notebook_manager.get_kernel_id(notebook_path)
        if kernel_id is None:
            return f"Notebook '{notebook_path}' has no kernel attached."

        notebook_manager.detach(notebook_path)
        logger.info(f"Detached kernel '{kernel_id}' from notebook '{notebook_path}'")
        return (
            f"Kernel '{kernel_id}' detached from notebook '{notebook_path}'.\n"
            f"The kernel is still running — use delete_kernel to stop it."
        )
