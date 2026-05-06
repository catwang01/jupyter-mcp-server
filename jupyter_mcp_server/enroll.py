# Copyright (c) 2024- Datalayer, Inc.
#
# BSD 3-Clause License

"""Auto-enrollment functionality for Jupyter MCP Server."""

import logging
from typing import Any

from jupyter_mcp_server.notebook_manager import NotebookManager

logger = logging.getLogger(__name__)


async def auto_enroll_document(
    config: Any,
    notebook_manager: NotebookManager,
    use_notebook_tool: Any,  # kept for backward compat, ignored
    server_context: Any,
) -> None:
    """Automatically enroll the configured document_id as a managed notebook.

    Creates a kernel (if start_new_runtime is True) or attaches an existing one
    (if runtime_id is provided), then associates it with config.document_id.

    Args:
        config: JupyterMCPConfig instance with configuration parameters
        notebook_manager: NotebookManager instance
        use_notebook_tool: Unused (kept for backward compat)
        server_context: ServerContext instance with server state
    """
    if not config.document_id:
        logger.debug("No document_id configured, skipping auto-enrollment")
        return

    if not config.runtime_id and not config.start_new_runtime:
        logger.info(
            f"No kernel configured for '{config.document_id}'. "
            "Use create_kernel + attach_kernel tools to associate a kernel."
        )
        return

    try:
        from jupyter_mcp_server.tools.create_kernel_tool import CreateKernelTool
        from jupyter_mcp_server.tools.attach_kernel_tool import AttachKernelTool

        if config.runtime_id:
            # Attach an existing kernel to the document
            kernel_id = config.runtime_id
            logger.info(
                f"Auto-enrolling '{config.document_id}' with existing kernel '{kernel_id}'"
            )
            result = await AttachKernelTool().execute(
                mode=server_context.mode,
                server_client=server_context.server_client,
                kernel_manager=server_context.kernel_manager,
                notebook_manager=notebook_manager,
                runtime_url=config.runtime_url if config.runtime_url != "local" else None,
                runtime_token=config.runtime_token,
                notebook_path=config.document_id,
                kernel_id=kernel_id,
            )
            logger.info(f"Auto-enrollment result: {result}")

        else:
            # Create a new kernel and attach it
            logger.info(f"Auto-enrolling '{config.document_id}' with a new kernel")
            result_str = await CreateKernelTool().execute(
                mode=server_context.mode,
                server_client=server_context.server_client,
                kernel_manager=server_context.kernel_manager,
                notebook_manager=notebook_manager,
                runtime_url=config.runtime_url if config.runtime_url != "local" else None,
                runtime_token=config.runtime_token,
            )
            logger.info(f"Kernel creation result: {result_str}")

            # Parse kernel_id from result ("Kernel created successfully.\nID: <id>\nName: <name>")
            kernel_id = None
            for line in result_str.splitlines():
                if line.startswith("ID: "):
                    kernel_id = line[4:].strip()
                    break

            if kernel_id:
                notebook_manager.attach(config.document_id, kernel_id)
                logger.info(
                    f"Auto-attached kernel '{kernel_id}' to '{config.document_id}'"
                )
            else:
                logger.warning(
                    f"Could not parse kernel_id from create_kernel result: {result_str!r}"
                )

    except Exception as e:
        logger.warning(
            f"Failed to auto-enroll document '{config.document_id}': {e}. "
            "Use create_kernel + attach_kernel tools manually."
        )
