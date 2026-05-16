# Copyright (c) 2024- Datalayer, Inc.
#
# BSD 3-Clause License

"""Execute IPython code directly in kernel tool."""

import asyncio
import logging
from typing import Union

from mcp.types import ImageContent

from jupyter_mcp_server.hooks import HookEvent, HookRegistry
from jupyter_mcp_server.tools._base import BaseTool, ServerMode
from jupyter_mcp_server.notebook_manager import NotebookManager

logger = logging.getLogger(__name__)


class ExecuteCodeTool(BaseTool):
    """Execute code directly in the kernel on the current active notebook"""
    
    async def _execute_via_kernel_manager(
        self,
        kernel_manager,
        kernel_id: str,
        code: str,
        timeout: int,
        safe_extract_outputs_fn
    ) -> list[Union[str, ImageContent]]:
        """Execute code using kernel_manager (JUPYTER_SERVER mode).

        Uses ExecutionStack (same path as execute_cell) to support gateway kernels.
        """
        from jupyter_mcp_server.utils import execute_via_execution_stack

        serverapp = kernel_manager.parent

        return await execute_via_execution_stack(
            serverapp=serverapp,
            kernel_id=kernel_id,
            code=code,
            timeout=timeout,
            logger=logger
        )
    
    async def _execute_via_notebook_manager(
        self,
        notebook_manager,
        code: str,
        timeout: int,
        wait_for_kernel_idle_fn,
        safe_extract_outputs_fn,
        kernel_id: str,
    ) -> list[Union[str, ImageContent]]:
        """Execute code using notebook_manager (MCP_SERVER mode)."""
        kernel = notebook_manager.get_kernel_client(kernel_id)
        if not kernel:
            return [f"[ERROR: Kernel '{kernel_id}' not found in manager.]"]

        await wait_for_kernel_idle_fn(kernel, max_wait_seconds=30)

        logger.info(f"Executing IPython code (MCP_SERVER) with timeout {timeout}s: {code[:100]}...")

        hooks = HookRegistry.get_instance()
        hook_ctx = await hooks.fire(
            HookEvent.BEFORE_EXECUTE,
            code=code, kernel_id=kernel_id, metadata={},
        )

        try:
            execution_task = asyncio.create_task(
                asyncio.to_thread(kernel.execute, code)
            )

            try:
                outputs = await asyncio.wait_for(execution_task, timeout=timeout)
            except asyncio.TimeoutError as e:
                execution_task.cancel()
                try:
                    if kernel and hasattr(kernel, 'interrupt'):
                        kernel.interrupt()
                except Exception as interrupt_err:
                    logger.error(f"Failed to interrupt kernel: {interrupt_err}")

                result = [f"[TIMEOUT ERROR: IPython execution exceeded {timeout} seconds and was interrupted]"]
                await hooks.fire(HookEvent.AFTER_EXECUTE, code=code, kernel_id=kernel_id,
                                 metadata={}, outputs=result, error=e, context=hook_ctx)
                return result

            if outputs:
                result = safe_extract_outputs_fn(outputs['outputs'])
                if not result:
                    result = ["[No output generated]"]
            else:
                result = ["[No output generated]"]

            await hooks.fire(HookEvent.AFTER_EXECUTE, code=code, kernel_id=kernel_id,
                             metadata={}, outputs=result, error=None, context=hook_ctx)
            return result

        except Exception as e:
            logger.error(f"Error executing IPython code: {e}")
            await hooks.fire(HookEvent.AFTER_EXECUTE, code=code, kernel_id=kernel_id,
                             metadata={}, outputs=[], error=e, context=hook_ctx)
            return [f"[ERROR: {type(e).__name__}: {e}]"]
    
    async def execute(
        self,
        mode: ServerMode,
        server_client=None,
        contents_manager=None,
        kernel_manager=None,
        kernel_spec_manager=None,
        notebook_manager=None,
        # Tool-specific parameters
        code: str = None,
        timeout: int = 60,
        kernel_id: str = None,
        wait_for_kernel_idle_fn=None,
        safe_extract_outputs_fn=None,
        **kwargs
    ) -> list[Union[str, ImageContent]]:
        """Execute IPython code directly in the kernel.
        
        Args:
            mode: Server mode (MCP_SERVER or JUPYTER_SERVER)
            server_client: JupyterServerClient (not used)
            contents_manager: Contents manager (not used)
            kernel_manager: Kernel manager (for JUPYTER_SERVER mode)
            kernel_spec_manager: Kernel spec manager (not used)
            notebook_manager: Notebook manager (for MCP_SERVER mode)
            code: IPython code to execute (supports magic commands, shell commands with !, and Python code)
            timeout: Execution timeout in seconds (default: 60s)
            kernel_id: Kernel ID (for JUPYTER_SERVER mode)
            ensure_kernel_alive_fn: Function to ensure kernel is alive (for MCP_SERVER mode)
            wait_for_kernel_idle_fn: Function to wait for kernel idle state (for MCP_SERVER mode)
            safe_extract_outputs_fn: Function to safely extract outputs
            
        Returns:
            List of outputs from the executed code
        """
        if safe_extract_outputs_fn is None:
            raise ValueError("safe_extract_outputs_fn is required")

        if not kernel_id:
            raise ValueError("kernel_id is required for execute_code")

        # JUPYTER_SERVER mode: Use kernel_manager directly
        if mode == ServerMode.JUPYTER_SERVER and kernel_manager is not None:
            logger.info(f"Executing IPython in JUPYTER_SERVER mode with kernel_id={kernel_id}")
            return await self._execute_via_kernel_manager(
                kernel_manager=kernel_manager,
                kernel_id=kernel_id,
                code=code,
                timeout=timeout,
                safe_extract_outputs_fn=safe_extract_outputs_fn
            )

        # MCP_SERVER mode: Use notebook_manager kernel client
        elif mode == ServerMode.MCP_SERVER and notebook_manager is not None:
            if wait_for_kernel_idle_fn is None:
                raise ValueError("wait_for_kernel_idle_fn is required for MCP_SERVER mode")
            logger.info("Executing IPython in MCP_SERVER mode")
            return await self._execute_via_notebook_manager(
                notebook_manager=notebook_manager,
                code=code,
                timeout=timeout,
                wait_for_kernel_idle_fn=wait_for_kernel_idle_fn,
                safe_extract_outputs_fn=safe_extract_outputs_fn,
                kernel_id=kernel_id,
            )

        else:
            return [f"[ERROR: Invalid mode or missing required managers]"]

