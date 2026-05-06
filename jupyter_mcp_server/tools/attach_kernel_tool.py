# Copyright (c) 2024- Datalayer, Inc.
#
# BSD 3-Clause License

"""Attach kernel tool implementation."""

import logging
from typing import Any, Optional

from jupyter_mcp_server.tools._base import BaseTool, ServerMode
from jupyter_mcp_server.notebook_manager import NotebookManager

logger = logging.getLogger(__name__)


class AttachKernelTool(BaseTool):
    """Associate a notebook path with an existing kernel."""

    async def execute(
        self,
        mode: ServerMode,
        server_client=None,
        kernel_manager: Optional[Any] = None,
        notebook_manager: Optional[NotebookManager] = None,
        runtime_url: Optional[str] = None,
        runtime_token: Optional[str] = None,
        notebook_path: str = "",
        kernel_id: str = "",
        **kwargs,
    ) -> str:
        if not notebook_path:
            raise ValueError("notebook_path is required")
        if not kernel_id:
            raise ValueError("kernel_id is required")

        # Verify the kernel exists
        if mode == ServerMode.JUPYTER_SERVER:
            if kernel_manager is None:
                raise ValueError("kernel_manager is required in JUPYTER_SERVER mode")
            if kernel_id not in kernel_manager:
                return (
                    f"Kernel '{kernel_id}' not found. "
                    f"Use list_kernels to see available kernels."
                )
            # Store metadata dict for JUPYTER_SERVER mode
            notebook_manager.add_kernel_client(kernel_id, {"id": kernel_id})

        elif mode == ServerMode.MCP_SERVER:
            if not runtime_url or not runtime_token:
                raise ValueError("runtime_url and runtime_token are required in MCP_SERVER mode")
            if server_client is None:
                raise ValueError("server_client is required in MCP_SERVER mode")
            # Verify kernel exists via HTTP
            kernels = server_client.kernels.list_kernels()
            if not any(k.id == kernel_id for k in kernels):
                return (
                    f"Kernel '{kernel_id}' not found. "
                    f"Use list_kernels to see available kernels."
                )
            # If we don't already have a KernelClient for this kernel, create one
            if notebook_manager.get_kernel_client(kernel_id) is None:
                from jupyter_kernel_client import KernelClient
                client = KernelClient(
                    server_url=runtime_url,
                    token=runtime_token,
                    kernel_id=kernel_id,
                )
                client.start(path=notebook_path)
                notebook_manager.add_kernel_client(kernel_id, client)
                logger.info(f"Connected KernelClient for kernel '{kernel_id}'")

        notebook_manager.attach(notebook_path, kernel_id)
        logger.info(f"Attached kernel '{kernel_id}' to notebook '{notebook_path}'")
        return (
            f"Kernel '{kernel_id}' attached to notebook '{notebook_path}'.\n"
            f"You can now execute cells in this notebook."
        )
