# Copyright (c) 2024- Datalayer, Inc.
#
# BSD 3-Clause License

"""Create kernel tool implementation."""

import asyncio
import logging
from typing import Any, Optional

from jupyter_mcp_server.tools._base import BaseTool, ServerMode
from jupyter_mcp_server.notebook_manager import NotebookManager

logger = logging.getLogger(__name__)


class CreateKernelTool(BaseTool):
    """Tool to create a new standalone kernel without attaching to a notebook."""

    async def _create_kernel_local(
        self,
        kernel_manager: Any,
        kernel_name: Optional[str] = None,
    ) -> dict:
        """Create a kernel using the local kernel_manager API (JUPYTER_SERVER mode)."""
        kwargs = {}
        if kernel_name:
            kwargs["kernel_name"] = kernel_name

        kernel_id = await kernel_manager.start_kernel(**kwargs)
        logger.info(f"Started kernel '{kernel_id}', waiting for it to be ready...")

        max_wait_time = 30
        wait_interval = 0.5
        elapsed = 0
        kernel_ready = False

        while elapsed < max_wait_time:
            try:
                kernel_model = kernel_manager.get_kernel(kernel_id)
                if kernel_model is not None:
                    try:
                        kernel_manager.get_connection_info(kernel_id)
                        kernel_ready = True
                        logger.info(f"Kernel '{kernel_id}' is ready (took {elapsed:.1f}s)")
                        break
                    except Exception:
                        pass
            except Exception as e:
                logger.debug(f"Waiting for kernel to start: {e}")

            await asyncio.sleep(wait_interval)
            elapsed += wait_interval

        if not kernel_ready:
            logger.warning(f"Kernel '{kernel_id}' may not be fully ready after {max_wait_time}s wait")

        return {"id": kernel_id, "name": kernel_name or "default"}

    def _create_kernel_http(
        self,
        runtime_url: str,
        runtime_token: str,
        kernel_name: Optional[str] = None,
    ) -> tuple:
        """Create a kernel using HTTP API (MCP_SERVER mode).

        Returns (kernel_client, result_dict).
        """
        from jupyter_kernel_client import KernelClient

        kernel = KernelClient(server_url=runtime_url, token=runtime_token)
        kernel.start()
        result = {"id": kernel.id, "name": getattr(kernel, "name", kernel_name or "unknown")}
        return kernel, result

    async def execute(
        self,
        mode: ServerMode,
        server_client=None,
        kernel_manager: Optional[Any] = None,
        kernel_spec_manager: Optional[Any] = None,
        notebook_manager: Optional[NotebookManager] = None,
        runtime_url: Optional[str] = None,
        runtime_token: Optional[str] = None,
        kernel_name: Optional[str] = None,
        **kwargs,
    ) -> str:
        """Create a new standalone kernel."""
        if mode == ServerMode.JUPYTER_SERVER:
            if kernel_manager is None:
                raise ValueError("kernel_manager is required in JUPYTER_SERVER mode")
            result = await self._create_kernel_local(kernel_manager, kernel_name)
            if notebook_manager is not None:
                notebook_manager.add_kernel_client(result["id"], {"id": result["id"]})

        elif mode == ServerMode.MCP_SERVER:
            if not runtime_url or not runtime_token:
                raise ValueError("runtime_url and runtime_token are required in MCP_SERVER mode")
            kernel_client, result = self._create_kernel_http(runtime_url, runtime_token, kernel_name)
            if notebook_manager is not None:
                notebook_manager.add_kernel_client(result["id"], kernel_client)

        else:
            raise ValueError(f"Unsupported mode: {mode}")

        return (
            f"Kernel created successfully.\n"
            f"ID: {result['id']}\n"
            f"Name: {result['name']}"
        )
