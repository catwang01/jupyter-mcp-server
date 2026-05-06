# Copyright (c) 2024- Datalayer, Inc.
#
# BSD 3-Clause License

"""Delete kernel tool implementation."""

import logging
from typing import Any, Optional

from jupyter_mcp_server.tools._base import BaseTool, ServerMode
from jupyter_mcp_server.notebook_manager import NotebookManager

logger = logging.getLogger(__name__)


class DeleteKernelTool(BaseTool):
    """Stop and remove a kernel. Also detaches it from all notebooks."""

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

        # Detach from all notebooks first
        detached_paths = notebook_manager.detach_by_kernel(kernel_id)

        if mode == ServerMode.JUPYTER_SERVER:
            if kernel_manager is None:
                raise ValueError("kernel_manager is required in JUPYTER_SERVER mode")
            try:
                await kernel_manager.shutdown_kernel(kernel_id)
                logger.info(f"Shut down kernel '{kernel_id}' via kernel_manager")
            except Exception as e:
                logger.warning(f"Failed to shut down kernel '{kernel_id}': {e}")
                return f"Failed to delete kernel '{kernel_id}': {e}"

        elif mode == ServerMode.MCP_SERVER:
            client = notebook_manager.get_kernel_client(kernel_id)
            if client and hasattr(client, "stop"):
                try:
                    client.stop()
                    logger.info(f"Stopped KernelClient for kernel '{kernel_id}'")
                except Exception as e:
                    logger.warning(f"Error stopping kernel client '{kernel_id}': {e}")

        notebook_manager.remove_kernel_client(kernel_id)

        msg = f"Kernel '{kernel_id}' deleted."
        if detached_paths:
            msg += f"\nDetached from: {', '.join(detached_paths)}"
        return msg
