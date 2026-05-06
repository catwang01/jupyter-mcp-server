# Copyright (c) 2024- Datalayer, Inc.
#
# BSD 3-Clause License

"""Restart kernel tool implementation."""

import logging
from typing import Any, Optional

from jupyter_mcp_server.tools._base import BaseTool, ServerMode
from jupyter_mcp_server.notebook_manager import NotebookManager

logger = logging.getLogger(__name__)


class RestartKernelTool(BaseTool):
    """Restart a kernel, clearing its memory state."""

    async def execute(
        self,
        mode: ServerMode,
        server_client=None,
        kernel_manager: Optional[Any] = None,
        notebook_manager: Optional[NotebookManager] = None,
        kernel_id: str = "",
        **kwargs,
    ) -> str:
        if not kernel_id:
            raise ValueError("kernel_id is required")

        if mode == ServerMode.JUPYTER_SERVER:
            if kernel_manager is None:
                raise ValueError("kernel_manager is required in JUPYTER_SERVER mode")
            try:
                await kernel_manager.restart_kernel(kernel_id)
                logger.info(f"Restarted kernel '{kernel_id}' via kernel_manager")
                return (
                    f"Kernel '{kernel_id}' restarted successfully. "
                    f"Memory state and imported packages have been cleared."
                )
            except Exception as e:
                logger.error(f"Failed to restart kernel '{kernel_id}': {e}")
                return f"Failed to restart kernel '{kernel_id}': {e}"

        elif mode == ServerMode.MCP_SERVER:
            success = notebook_manager.restart_kernel_client(kernel_id)
            if success:
                return (
                    f"Kernel '{kernel_id}' restarted successfully. "
                    f"Memory state and imported packages have been cleared."
                )
            else:
                return f"Failed to restart kernel '{kernel_id}'. Kernel not found or does not support restart."

        raise ValueError(f"Unsupported mode: {mode}")
