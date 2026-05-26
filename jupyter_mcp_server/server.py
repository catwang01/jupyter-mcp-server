# Copyright (c) 2024- Datalayer, Inc.
#
# BSD 3-Clause License

"""
Jupyter MCP Server Layer
"""

from typing import Annotated, Literal, Optional
from pydantic import Field
from fastapi import Request
from jupyter_kernel_client import KernelClient

from mcp.server import FastMCP
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.types import ImageContent, ToolAnnotations
from starlette.middleware.cors import CORSMiddleware
from starlette.applications import Starlette
from starlette.responses import JSONResponse

from jupyter_mcp_server.log import logger
from jupyter_mcp_server.models import DocumentRuntime
from jupyter_mcp_server.utils import (
    safe_extract_outputs,
    create_kernel,
    wait_for_kernel_idle,
    safe_notebook_operation
)
from jupyter_mcp_server.config import get_config, set_config
from jupyter_mcp_server.notebook_manager import NotebookManager
from jupyter_mcp_server.server_context import ServerContext
from jupyter_mcp_server.enroll import auto_enroll_document
from jupyter_mcp_server.hooks import HookEvent, HookRegistry, with_hooks
from jupyter_mcp_server.tools import (
    # Tool infrastructure
    ServerMode,
    # Cell Reading
    ReadNotebookTool,
    ReadCellTool,
    # Cell Writing
    InsertCellTool,
    OverwriteCellSourceTool,
    EditCellSourceTool,
    DeleteCellTool,
    MoveCellTool,
    # Cell Execution
    ExecuteCellTool,
    # Kernel Management
    CreateKernelTool,
    DeleteKernelTool,
    RestartKernelTool,
    ListKernelsTool,
    # Notebook Status
    ListNotebooksTool,
    # Kernel-Notebook Association
    AttachKernelTool,
    DetachKernelTool,
    # Other Tools
    ExecuteCodeTool,
    ListFilesTool,
    ConnectJupyterTool,
    # MCP Prompt
    JupyterCitePrompt,
)


###############################################################################
# Globals.

class RuntimeTokenVerifier:
    """Verify MCP client requests against the configured runtime token."""

    def __init__(self, token: str):
        self._token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        if token != self._token:
            return None
        return AccessToken(token=token, client_id="mcp-client", scopes=[])


class FastMCPWithCORS(FastMCP):
    def streamable_http_app(self) -> Starlette:
        """Return StreamableHTTP server app with CORS and auth middleware.

        See: https://github.com/modelcontextprotocol/python-sdk/issues/187
        """
        # Get the original Starlette app (includes RequireAuthMiddleware
        # when _token_verifier is set, but NOT the AuthenticationMiddleware
        # that actually validates Bearer tokens — that requires settings.auth
        # which we don't use). Add it here directly.
        app = super().streamable_http_app()

        if self._token_verifier:
            from starlette.middleware.authentication import AuthenticationMiddleware
            from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend
            app.add_middleware(
                AuthenticationMiddleware,
                backend=BearerAuthBackend(self._token_verifier),
            )

        # Add CORS middleware
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # In production, should set specific domains
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
        return app

mcp = FastMCPWithCORS(name="Jupyter MCP Server", json_response=False, stateless_http=True)
notebook_manager = NotebookManager()
server_context = ServerContext.get_instance()

def __start_kernel():
    """Start the default kernel for legacy /api/connect route."""
    config = get_config()
    kernel = create_kernel(config, logger)
    notebook_manager.set_default_kernel(kernel.id, kernel)

async def __auto_enroll_document():
    """Wrapper for auto_enroll_document that uses server context."""
    await auto_enroll_document(
        config=get_config(),
        notebook_manager=notebook_manager,
        use_notebook_tool=None,
        server_context=server_context,
    )


###############################################################################
# Custom Routes.


@mcp.custom_route("/api/connect", ["PUT"])
async def connect(request: Request):
    """Connect to a document and a runtime from the Jupyter MCP server."""

    data = await request.json()
    
    # Log the received data for diagnostics
    # Note: set_config() will automatically normalize string "None" values
    logger.info(
        f"Connect endpoint received - runtime_url: {repr(data.get('runtime_url'))}, "
        f"document_url: {repr(data.get('document_url'))}, "
        f"provider: {data.get('provider')}"
    )

    document_runtime = DocumentRuntime(**data)

    # Clean up existing default kernel if any
    if notebook_manager.get_default_kernel() is not None:
        try:
            notebook_manager.clear_default_kernel()
        except Exception as e:
            logger.warning(f"Error stopping existing kernel during connect: {e}")

    # Update configuration with new values
    # String "None" values will be automatically normalized by set_config()
    set_config(
        provider=document_runtime.provider,
        runtime_url=document_runtime.runtime_url,
        runtime_id=document_runtime.runtime_id,
        runtime_token=document_runtime.runtime_token,
        document_url=document_runtime.document_url,
        document_id=document_runtime.document_id,
        document_token=document_runtime.document_token,
        allowed_jupyter_tools=document_runtime.allowed_jupyter_tools or "notebook_run-all-cells,notebook_get-selected-cell"
    )
    
    # Reset ServerContext to pick up new configuration
    ServerContext.reset()

    try:
        __start_kernel()
        return JSONResponse({"success": True})
    except Exception as e:
        logger.error(f"Failed to connect: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@mcp.custom_route("/api/stop", ["DELETE"])
async def stop(request: Request):
    try:
        notebook_manager.clear_default_kernel()
        return JSONResponse({"success": True})
    except Exception as e:
        logger.error(f"Error stopping kernel: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


@mcp.custom_route("/api/healthz", ["GET"])
async def health_check(request: Request):
    """Custom health check endpoint"""
    kernel_status = "unknown"
    try:
        kernel = notebook_manager.get_default_kernel()
        if kernel:
            kernel_status = "alive" if hasattr(kernel, 'is_alive') and kernel.is_alive() else "dead"
        else:
            kernel_status = "not_initialized"
    except Exception:
        kernel_status = "error"
    return JSONResponse(
        {
            "success": True,
            "service": "jupyter-mcp-server",
            "message": "Jupyter MCP Server is running.",
            "status": "healthy",
            "kernel_status": kernel_status,
        }
    )


###############################################################################
# Tools.
###############################################################################

###############################################################################
# Server Management Tools.

@mcp.tool(
    annotations=ToolAnnotations(
        title="List Files",
        readOnlyHint=True,
    ),
)
@with_hooks("list_files")
async def list_files(
    path: Annotated[str, Field(description="The starting path to list from (empty string means root directory)")] = "",
    # Maximum depth to recurse into subdirectories, Set Max to 3 to avoid infinite recursion.
    max_depth: Annotated[int, Field(description="Maximum depth to recurse into subdirectories", ge=0, le=3)] = 1,
    start_index: Annotated[int, Field(description="Starting index for pagination (0-based)", ge=0)] = 0,
    limit: Annotated[int, Field(description="Maximum number of items to return (0 means no limit)", ge=0)] = 25,
    pattern: Annotated[str, Field(description="Glob pattern to filter file paths")] = "",
) -> Annotated[str, Field(description="Tab-separated table with columns: Path, Type, Size, Last_Modified. Includes pagination info header.")]:
    """
    List all files and directories recursively in the Jupyter server's file system.
    Used to explore the file system structure of the Jupyter server or to find specific files or directories.
    """
    return await safe_notebook_operation(
        lambda: ListFilesTool().execute(
            mode=server_context.mode,
            server_client=server_context.server_client,
            contents_manager=server_context.contents_manager,
            path=path,
            max_depth=max_depth,
            start_index=start_index,
            limit=limit,
            pattern=pattern if pattern else None,
        )
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="List Kernels",
        readOnlyHint=True,
    ),
)
@with_hooks("list_kernels")
async def list_kernels() -> Annotated[str, Field(description="Two-level output. Level 1: kernel spec header '[name]  Display Name  (language)'. Level 2: indented running instances with id/state/connections/last_activity, or '(not running)' if none.")]:
    """List all kernel specs and their running instances on the Jupyter server.

    Shows every available kernel spec as a top-level entry, with each running
    instance listed beneath it. Useful for choosing a kernel_name when calling
    create_kernel, as well as monitoring active kernels.
    """
    return await safe_notebook_operation(
        lambda: ListKernelsTool().execute(
            mode=server_context.mode,
            server_client=server_context.server_client,
            kernel_manager=server_context.kernel_manager,
            kernel_spec_manager=server_context.kernel_spec_manager,
        )
    )

###############################################################################
# Kernel Management Tools.


@mcp.tool(
    annotations=ToolAnnotations(
        title="Create Kernel",
        destructiveHint=True,
    ),
)
@with_hooks("create_kernel")
async def create_kernel(
    kernel_name: Annotated[Optional[str], Field(description="Kernel spec name (e.g. 'python3', 'ir'). Uses server default if not specified.")] = None,
) -> Annotated[str, Field(description="Kernel ID and name")]:
    """Create a new standalone kernel.

    Returns the kernel ID. Use attach_kernel to associate it with a notebook before executing cells.
    """
    config = get_config()
    return await safe_notebook_operation(
        lambda: CreateKernelTool().execute(
            mode=server_context.mode,
            server_client=server_context.server_client,
            kernel_manager=server_context.kernel_manager,
            notebook_manager=notebook_manager,
            runtime_url=config.runtime_url if config.runtime_url != "local" else None,
            runtime_token=config.runtime_token,
            kernel_name=kernel_name,
        )
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Kernel",
        destructiveHint=True,
    ),
)
@with_hooks("delete_kernel")
async def delete_kernel(
    kernel_id: Annotated[str, Field(description="ID of the kernel to delete")],
) -> Annotated[str, Field(description="Success message")]:
    """Stop and delete a kernel. Also detaches it from all associated notebooks."""
    result = await safe_notebook_operation(
        lambda: DeleteKernelTool().execute(
            mode=server_context.mode,
            kernel_manager=server_context.kernel_manager,
            notebook_manager=notebook_manager,
            kernel_id=kernel_id,
        )
    )
    await HookRegistry.get_instance().fire(
        HookEvent.KERNEL_LIFECYCLE,
        event_type="stopped",
        kernel_id=kernel_id,
        kernel_name=kernel_id,
    )
    return result


@mcp.tool(
    annotations=ToolAnnotations(
        title="Restart Kernel",
        destructiveHint=True,
    ),
)
@with_hooks("restart_kernel")
async def restart_kernel(
    kernel_id: Annotated[str, Field(description="ID of the kernel to restart")],
) -> Annotated[str, Field(description="Success message")]:
    """Restart a kernel, clearing its memory state and imported packages."""
    result = await safe_notebook_operation(
        lambda: RestartKernelTool().execute(
            mode=server_context.mode,
            kernel_manager=server_context.kernel_manager,
            notebook_manager=notebook_manager,
            kernel_id=kernel_id,
        )
    )
    await HookRegistry.get_instance().fire(
        HookEvent.KERNEL_LIFECYCLE,
        event_type="restarted",
        kernel_id=kernel_id,
        kernel_name=kernel_id,
    )
    return result


###############################################################################
# Kernel-Notebook Association Tools.


@mcp.tool(
    annotations=ToolAnnotations(
        title="Attach Kernel",
        destructiveHint=True,
    ),
)
@with_hooks("attach_kernel")
async def attach_kernel(
    notebook_path: Annotated[str, Field(description="Path to the notebook file, relative to the Jupyter server root (e.g. 'notebook.ipynb')")],
    kernel_id: Annotated[str, Field(description="ID of the kernel to attach")],
) -> Annotated[str, Field(description="Success message")]:
    """Associate a kernel with a notebook path, enabling cell execution.

    After attaching, execute_cell and insert_execute_code_cell will use this kernel.
    A notebook can only have one kernel attached at a time.
    """
    config = get_config()
    return await safe_notebook_operation(
        lambda: AttachKernelTool().execute(
            mode=server_context.mode,
            server_client=server_context.server_client,
            kernel_manager=server_context.kernel_manager,
            notebook_manager=notebook_manager,
            runtime_url=config.runtime_url if config.runtime_url != "local" else None,
            runtime_token=config.runtime_token,
            notebook_path=notebook_path,
            kernel_id=kernel_id,
        )
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Detach Kernel",
        destructiveHint=True,
    ),
)
@with_hooks("detach_kernel")
async def detach_kernel(
    notebook_path: Annotated[str, Field(description="Path to the notebook file")],
) -> Annotated[str, Field(description="Success message")]:
    """Remove the kernel association from a notebook. The kernel keeps running."""
    return await safe_notebook_operation(
        lambda: DetachKernelTool().execute(
            mode=server_context.mode,
            notebook_manager=notebook_manager,
            notebook_path=notebook_path,
        )
    )


###############################################################################
# Notebook Read/Write Tools.


@mcp.tool(
    annotations=ToolAnnotations(
        title="List Notebooks",
        readOnlyHint=True,
    ),
)
@with_hooks("list_notebooks")
async def list_notebooks() -> Annotated[str, Field(description="Tab-separated table with columns: Notebook_Path, Kernel_ID")]:
    """List all notebooks and their attached kernel IDs.

    Shows the current notebook → kernel attachment status maintained by the
    notebook manager. Only notebooks that have been explicitly attached via
    attach_kernel appear here.
    """
    return await safe_notebook_operation(
        lambda: ListNotebooksTool().execute(
            mode=server_context.mode,
            notebook_manager=notebook_manager,
        )
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Read Notebook",
        readOnlyHint=True,
    ),
)
@with_hooks("read_notebook")
async def read_notebook(
    notebook_path: Annotated[str, Field(description="Path to the notebook file, relative to the Jupyter server root")],
    response_format: Annotated[Literal["brief", "detailed"], Field(description="'brief' returns first line and line count; 'detailed' returns full cell source")] = "brief",
    start_index: Annotated[int, Field(description="Starting cell index for pagination (0-based)", ge=0)] = 0,
    limit: Annotated[int, Field(description="Maximum number of cells to return (0 = no limit)", ge=0)] = 20,
) -> Annotated[str, Field(description="Notebook content in the requested format")]:
    """Read a notebook and return index, source content, type, and execution count of each cell."""
    return await safe_notebook_operation(
        lambda: ReadNotebookTool().execute(
            mode=server_context.mode,
            server_client=server_context.server_client,
            contents_manager=server_context.contents_manager,
            notebook_manager=notebook_manager,
            notebook_path=notebook_path,
            response_format=response_format,
            start_index=start_index,
            limit=limit,
        )
    )

###############################################################################
# Cell Tools.

@mcp.tool(
    annotations=ToolAnnotations(
        title="Insert Cell",
        destructiveHint=True,
    ),
)
@with_hooks("insert_cell")
async def insert_cell(
    notebook_path: Annotated[str, Field(description="Path to the notebook file, relative to the Jupyter server root")],
    cell_type: Annotated[Literal["code", "markdown"], Field(description="Type of cell to insert")],
    cell_source: Annotated[str, Field(description="Source content for the cell")],
    cell_index: Annotated[Optional[int], Field(description="Target index for insertion (0-based), use -1 to append at end. Required if cell_id not provided.", ge=-1)] = None,
    cell_id: Annotated[Optional[str], Field(description="Stable cell ID (nbformat 4.5+) of the reference cell. Use with insert_position.")] = None,
    insert_position: Annotated[Literal["before", "after"], Field(description='Insert "before" or "after" the cell identified by cell_id. Ignored when cell_index is used.')] = "after",
) -> Annotated[str, Field(description="Success message and the structure of its surrounding cells")]:
    """Insert a cell at a specified position in the notebook.

    Provide either cell_index (integer position) or cell_id + insert_position (stable ID-based insertion).
    """
    return await safe_notebook_operation(
        lambda: InsertCellTool().execute(
            mode=server_context.mode,
            server_client=server_context.server_client,
            contents_manager=server_context.contents_manager,
            kernel_manager=server_context.kernel_manager,
            notebook_manager=notebook_manager,
            notebook_path=notebook_path,
            cell_index=cell_index,
            cell_source=cell_source,
            cell_type=cell_type,
            cell_id=cell_id,
            insert_position=insert_position,
        )
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Overwrite Cell Source",
        destructiveHint=True,
    ),
)
@with_hooks("overwrite_cell_source")
async def overwrite_cell_source(
    notebook_path: Annotated[str, Field(description="Path to the notebook file, relative to the Jupyter server root")],
    cell_source: Annotated[str, Field(description="New complete cell source")],
    cell_index: Annotated[Optional[int], Field(description="Index of the cell to overwrite (0-based). Required if cell_id not provided.", ge=0)] = None,
    cell_id: Annotated[Optional[str], Field(description="Stable cell ID (nbformat 4.5+). Prefer over cell_index when available.")] = None,
) -> Annotated[str, Field(description="Success message with diff showing changes made")]:
    """Replace the entire source of a cell. Prefer cell_id over cell_index when available."""
    return await safe_notebook_operation(
        lambda: OverwriteCellSourceTool().execute(
            mode=server_context.mode,
            server_client=server_context.server_client,
            contents_manager=server_context.contents_manager,
            kernel_manager=server_context.kernel_manager,
            notebook_manager=notebook_manager,
            notebook_path=notebook_path,
            cell_index=cell_index,
            cell_id=cell_id,
            cell_source=cell_source,
        )
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Edit Cell Source",
        destructiveHint=True,
    ),
)
async def edit_cell_source(
    notebook_path: Annotated[str, Field(description="Path to the notebook file, relative to the Jupyter server root")],
    old_string: Annotated[str, Field(description="Exact string to find in cell source")],
    new_string: Annotated[str, Field(description="Replacement string")],
    cell_index: Annotated[Optional[int], Field(description="Index of the cell to edit (0-based). Required if cell_id not provided.", ge=0)] = None,
    replace_all: Annotated[bool, Field(description="Replace all occurrences (default: first only)")] = False,
    cell_id: Annotated[Optional[str], Field(description="Stable cell ID (nbformat 4.5+). Prefer over cell_index when available.")] = None,
) -> Annotated[str, Field(description="Success message with diff showing changes made")]:
    """Surgical find-and-replace within a cell's source. Prefer cell_id over cell_index when available."""
    return await safe_notebook_operation(
        lambda: EditCellSourceTool().execute(
            mode=server_context.mode,
            server_client=server_context.server_client,
            contents_manager=server_context.contents_manager,
            kernel_manager=server_context.kernel_manager,
            notebook_manager=notebook_manager,
            notebook_path=notebook_path,
            cell_index=cell_index,
            cell_id=cell_id,
            old_string=old_string,
            new_string=new_string,
            replace_all=replace_all,
        )
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Execute Cell",
        destructiveHint=True,
    ),
    structured_output=False,
)
@with_hooks("execute_cell")
async def execute_cell(
    notebook_path: Annotated[str, Field(description="Path to the notebook file, relative to the Jupyter server root")],
    cell_index: Annotated[Optional[int], Field(description="Index of the cell to execute (0-based). Required if cell_id not provided.", ge=0)] = None,
    cell_id: Annotated[Optional[str], Field(description="Stable cell ID (nbformat 4.5+). Prefer over cell_index when available.")] = None,
    timeout: Annotated[int, Field(description="Maximum seconds to wait for execution")] = 90,
    stream: Annotated[bool, Field(description="Enable streaming progress updates for long-running cells")] = False,
    progress_interval: Annotated[int, Field(description="Seconds between progress updates when stream=True")] = 5,
) -> Annotated[list[str | ImageContent], Field(description="List of outputs from the executed cell")]:
    """Execute a cell. Requires a kernel to be attached to this notebook via attach_kernel. Prefer cell_id when available. Fall back to cell_index when cell IDs are not available."""
    return await safe_notebook_operation(
        lambda: ExecuteCellTool().execute(
            mode=server_context.mode,
            server_client=server_context.server_client,
            contents_manager=server_context.contents_manager,
            kernel_manager=server_context.kernel_manager,
            notebook_manager=notebook_manager,
            notebook_path=notebook_path,
            cell_index=cell_index,
            cell_id=cell_id,
            timeout_seconds=timeout,
            stream=stream,
            progress_interval=progress_interval,
        ),
        max_retries=1
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Insert and Execute Code Cell",
        destructiveHint=True,
    ),
    structured_output=False,
)
@with_hooks("insert_execute_code_cell")
async def insert_execute_code_cell(
    notebook_path: Annotated[str, Field(description="Path to the notebook file, relative to the Jupyter server root")],
    cell_source: Annotated[str, Field(description="Code source for the cell")],
    cell_index: Annotated[Optional[int], Field(description="Index at which to insert and execute (0-based). Required if cell_id not provided.", ge=-1)] = None,
    cell_id: Annotated[Optional[str], Field(description="Stable cell ID. Insert relative to this cell using insert_position.")] = None,
    insert_position: Annotated[Literal["before", "after"], Field(description='Insert "before" or "after" the cell identified by cell_id.')] = "after",
    timeout: Annotated[int, Field(description="Maximum seconds to wait for execution")] = 90,
) -> Annotated[list[str | ImageContent], Field(description="List of outputs from the executed cell")]:
    """Insert a code cell and immediately execute it. Requires a kernel attached via attach_kernel.

    Provide either cell_index (integer position) or cell_id + insert_position (stable ID-based insertion).
    """
    import re

    insert_result = await safe_notebook_operation(
        lambda: InsertCellTool().execute(
            mode=server_context.mode,
            server_client=server_context.server_client,
            contents_manager=server_context.contents_manager,
            kernel_manager=server_context.kernel_manager,
            notebook_manager=notebook_manager,
            notebook_path=notebook_path,
            cell_index=cell_index,
            cell_source=cell_source,
            cell_type="code",
            cell_id=cell_id,
            insert_position=insert_position,
        )
    )

    # Extract actual insertion index from result string (e.g. "Cell inserted successfully at index 3 ...")
    match = re.search(r"at index (\d+)", insert_result)
    actual_index = int(match.group(1)) if match else cell_index

    return await safe_notebook_operation(
        lambda: ExecuteCellTool().execute(
            mode=server_context.mode,
            server_client=server_context.server_client,
            contents_manager=server_context.contents_manager,
            kernel_manager=server_context.kernel_manager,
            notebook_manager=notebook_manager,
            notebook_path=notebook_path,
            cell_index=actual_index,
            timeout_seconds=timeout,
            stream=False,
            progress_interval=0,
        ),
        max_retries=1
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Read Cell",
        readOnlyHint=True,
    ),
    structured_output=False,
)
@with_hooks("read_cell")
async def read_cell(
    notebook_path: Annotated[str, Field(description="Path to the notebook file, relative to the Jupyter server root")],
    cell_index: Annotated[Optional[int], Field(description="Index of the cell to read (0-based). Required if cell_id not provided.", ge=0)] = None,
    include_outputs: Annotated[bool, Field(description="Include outputs in the response (only for code cells)")] = True,
    cell_id: Annotated[Optional[str], Field(description="Stable cell ID (nbformat 4.5+). Prefer over cell_index when available.")] = None,
) -> Annotated[list[str | ImageContent], Field(description="Cell information including index, type, source, and outputs")]:
    """Read a specific cell from a notebook.

Prefer cell_id when available (shown in read_notebook output). Fall back to cell_index (0-based) when cell IDs are not available.
"""
    return await safe_notebook_operation(
        lambda: ReadCellTool().execute(
            mode=server_context.mode,
            server_client=server_context.server_client,
            contents_manager=server_context.contents_manager,
            notebook_manager=notebook_manager,
            notebook_path=notebook_path,
            cell_index=cell_index,
            cell_id=cell_id,
            include_outputs=include_outputs,
        )
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Delete Cell",
        destructiveHint=True,
    ),
)
@with_hooks("delete_cell")
async def delete_cell(
    notebook_path: Annotated[str, Field(description="Path to the notebook file, relative to the Jupyter server root")],
    cell_indices: Annotated[Optional[list[int]], Field(description="List of cell indices to delete (0-based). Required if cell_ids not provided.")] = None,
    cell_ids: Annotated[Optional[list[str]], Field(description="List of stable cell IDs to delete. Merged with cell_indices if both provided.")] = None,
    include_source: Annotated[bool, Field(description="Whether to include the source of deleted cells")] = True,
) -> Annotated[str, Field(description="Success message with list of deleted cells")]:
    """Delete specific cells from a notebook. Prefer cell_ids when available. Both cell_ids and cell_indices can be provided simultaneously (union)."""
    return await safe_notebook_operation(
        lambda: DeleteCellTool().execute(
            mode=server_context.mode,
            server_client=server_context.server_client,
            contents_manager=server_context.contents_manager,
            kernel_manager=server_context.kernel_manager,
            notebook_manager=notebook_manager,
            notebook_path=notebook_path,
            cell_indices=cell_indices,
            cell_ids=cell_ids,
            include_source=include_source,
        )
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Move Cell",
        destructiveHint=True,
    ),
)
async def move_cell(
    notebook_path: Annotated[str, Field(description="Path to the notebook file, relative to the Jupyter server root")],
    source_index: Annotated[Optional[int], Field(description="Index of the cell to move (0-based). Required if source_cell_id not provided.", ge=0)] = None,
    target_index: Annotated[Optional[int], Field(description="Destination index (0-based). Required if target_cell_id not provided.", ge=0)] = None,
    source_cell_id: Annotated[Optional[str], Field(description="Stable cell ID of the cell to move.")] = None,
    target_cell_id: Annotated[Optional[str], Field(description="Stable cell ID of the destination position.")] = None,
) -> Annotated[str, Field(description="Success message with moved cell info")]:
    """Move a cell from one position to another within a notebook. Prefer source_cell_id/target_cell_id when available. Can mix: e.g., source_cell_id + target_index."""
    return await safe_notebook_operation(
        lambda: MoveCellTool().execute(
            mode=server_context.mode,
            server_client=server_context.server_client,
            contents_manager=server_context.contents_manager,
            kernel_manager=server_context.kernel_manager,
            notebook_manager=notebook_manager,
            notebook_path=notebook_path,
            source_index=source_index,
            target_index=target_index,
            source_cell_id=source_cell_id,
            target_cell_id=target_cell_id,
        )
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Execute Code",
        destructiveHint=True,
    ),
    structured_output=False,
)
@with_hooks("execute_code")
async def execute_code(
    kernel_id: Annotated[str, Field(description="ID of the kernel to execute code in")],
    code: Annotated[str, Field(description="Code to execute (supports magic commands with %, shell commands with !)")],
    timeout: Annotated[int, Field(description="Execution timeout in seconds", le=60)] = 30,
) -> Annotated[list[str | ImageContent], Field(description="List of outputs from the executed code")]:
    """Execute code directly in a kernel (not saved to notebook).

    Use for: magic commands (%timeit, %pip install), profiling, inspecting variables,
    temporary calculations, shell commands (!git ...).
    """
    return await safe_notebook_operation(
        lambda: ExecuteCodeTool().execute(
            mode=server_context.mode,
            server_client=server_context.server_client,
            kernel_manager=server_context.kernel_manager,
            notebook_manager=notebook_manager,
            code=code,
            timeout=timeout,
            kernel_id=kernel_id,
            wait_for_kernel_idle_fn=wait_for_kernel_idle,
            safe_extract_outputs_fn=safe_extract_outputs,
        ),
        max_retries=1
    )


@mcp.tool(
    annotations=ToolAnnotations(
        title="Connect to Jupyter Server",
        destructiveHint=True,
    ),
)
@with_hooks("connect_to_jupyter")
async def connect_to_jupyter(
    jupyter_url: Annotated[str, Field(description="Jupyter server URL to connect to (e.g., 'http://localhost:8888')")],
    jupyter_token: Annotated[Optional[str], Field(description="Jupyter server authentication token")] = None,
    provider: Annotated[str, Field(description="Provider type")] = "jupyter",
) -> Annotated[str, Field(description="Connection status message")]:
    """Connect to a Jupyter server dynamically with URL and token.
    
    This tool allows you to connect to different Jupyter servers without needing to 
    restart the MCP server or modify configuration files. Particularly useful when:
    - Working with multiple Jupyter servers with different ports/tokens
    - Jupyter server token changes dynamically
    - Need to switch between different Jupyter instances
    
    Example usage:
    - "Connect to http://localhost:8888 with token abc123"
    - "Connect to http://localhost:8889 without authentication"
    """
    return await safe_notebook_operation(
        lambda: ConnectJupyterTool().execute(
            mode=server_context.mode,
            jupyter_url=jupyter_url,
            jupyter_token=jupyter_token,
            provider=provider,
        )
    )

###############################################################################
# Prompt

@mcp.prompt()
async def jupyter_cite(
    prompt: Annotated[str, Field(description="User prompt for the cited cells")],
    cell_indices: Annotated[str, Field(description="Cell indices to cite (0-based),supporting flexible range format, e.g., '0,1,2', '0-2' or '0-2,4'")],
    notebook_path: Annotated[str, Field(description="Path to the notebook file, relative to the Jupyter server root")],
):
    """
    Like @ or # in Coding IDE or CLI, cite specific cells from specified notebook and insert them into the prompt.
    """
    return await safe_notebook_operation(
        lambda: JupyterCitePrompt().execute(
            mode=server_context.mode,
            server_client=server_context.server_client,
            contents_manager=server_context.contents_manager,
            notebook_manager=notebook_manager,
            cell_indices=cell_indices,
            notebook_path=notebook_path,
            prompt=prompt,
        )
    )

###############################################################################
# Helper Functions for Extension.


async def get_registered_tools():
    """
    Get list of all registered MCP tools with their metadata.
    
    This function is used by the Jupyter extension to dynamically expose
    the tool registry without hardcoding tool names and parameters.
    
    For JUPYTER_SERVER mode, it queries the jupyter-mcp-tools extension.
    For MCP_SERVER mode, it uses the local FastMCP registry.
    
    Returns:
        list: List of tool dictionaries with name, description, and inputSchema
    """
    context = ServerContext.get_instance()
    mode = context._mode
    
    # For JUPYTER_SERVER mode, expose BOTH FastMCP tools AND jupyter-mcp-tools (when enabled)
    if mode == ServerMode.JUPYTER_SERVER:
        all_tools = []
        jupyter_tool_names = set()
        
        # Check if JupyterLab mode is enabled before loading jupyter-mcp-tools
        if server_context.is_jupyterlab_mode():
            logger.info("JupyterLab mode enabled, loading selected jupyter-mcp-tools")
            
            # Get tools from jupyter-mcp-tools extension with caching
            try:
                from jupyter_mcp_tools import get_tools
                from jupyter_mcp_server.tool_cache import get_tool_cache
                
                # Get the base_url and token from server context
                # In JUPYTER_SERVER mode, we should use the actual serverapp URL, not hardcoded localhost
                if server_context.serverapp is not None:
                    # Use the actual Jupyter server connection URL
                    base_url = server_context.serverapp.connection_url
                    token = server_context.serverapp.token
                    logger.info(f"Using Jupyter ServerApp connection URL: {base_url}")
                else:
                    # Fallback to configuration (for remote scenarios)
                    config = get_config()
                    base_url = config.runtime_url if config.runtime_url else "http://localhost:8888"
                    token = config.runtime_token
                    logger.info(f"Using config runtime URL: {base_url}")
                
                logger.info(f"Querying jupyter-mcp-tools at {base_url}")
                
                # Define specific tools we want to load from jupyter-mcp-tools
                # (https://github.com/datalayer/jupyter-mcp-tools)
                # jupyter-mcp-tools exposes JupyterLab commands as MCP tools.
                # Only tools listed here will be available to MCP clients.
                # To add new tools, also update the list in handlers.py and
                # see docs/docs/reference/tools-additional/index.mdx for documentation.
                config = get_config()
                allowed_jupyter_mcp_tools = config.get_allowed_jupyter_mcp_tools()
                
                # Try querying with caching to avoid expensive repeated calls
                try:
                    search_query = ",".join(allowed_jupyter_mcp_tools)
                    logger.info(f"Searching jupyter-mcp-tools with query: '{search_query}' (allowed_tools: {allowed_jupyter_mcp_tools})")
                    
                    # Use cached get_tools to avoid expensive repeated calls
                    tool_cache = get_tool_cache()
                    tools_data = await tool_cache.get_tools(
                        base_url=base_url,
                        token=token,
                        query=search_query,
                        enabled_only=False,
                        fetch_func=get_tools  # Pass the actual get_tools function for cache misses
                    )
                    logger.info(f"Query returned {len(tools_data)} tools (from cache or fresh)")
                    
                    # Use the tools directly since query should return only what we want
                    for tool in tools_data:
                        logger.info(f"Found tool: {tool.get('id', '')}")
                    
                except Exception as e:
                    logger.warning(f"Failed to load jupyter-mcp-tools: {e}")
                    tools_data = []
                
                logger.info(f"Successfully loaded {len(tools_data)} specific jupyter-mcp-tools")
                
                logger.info(f"Retrieved {len(tools_data)} tools from jupyter-mcp-tools extension")
                
                # Convert jupyter-mcp-tools format to MCP format
                for tool_data in tools_data:
                    tool_name = tool_data.get('id', '')
                    jupyter_tool_names.add(tool_name)
                    
                    # Only include MCP protocol fields (exclude internal fields like commandId)
                    tool_dict = {
                        "name": tool_name,
                        "description": tool_data.get('caption', tool_data.get('label', '')),
                    }
                    
                    # Convert parameters to inputSchema
                    # The parameters field contains the JSON Schema for the tool's arguments
                    params = tool_data.get('parameters', {})
                    if params and isinstance(params, dict) and params.get('properties'):
                        # Tool has parameters - use them as inputSchema
                        tool_dict["inputSchema"] = params
                        tool_dict["parameters"] = list(params['properties'].keys())
                        logger.debug(f"Tool {tool_dict['name']} has parameters: {tool_dict['parameters']}")
                    else:
                        # Tool has no parameters - use empty schema
                        tool_dict["parameters"] = []
                        tool_dict["inputSchema"] = {
                            "type": "object",
                            "properties": {},
                            "description": tool_data.get('usage', '')
                        }
                    
                    all_tools.append(tool_dict)
                
                logger.info(f"Converted {len(all_tools)} tool(s) from jupyter-mcp-tools with parameter schemas")
                
            except Exception as e:
                logger.error(f"Error querying jupyter-mcp-tools extension: {e}", exc_info=True)
                # Continue to add FastMCP tools even if jupyter-mcp-tools fails
        else:
            logger.info("JupyterLab mode disabled, skipping jupyter-mcp-tools integration")
        
        # Second, add FastMCP tools
        try:
            tools_list = await mcp.list_tools()
            logger.info(f"Retrieved {len(tools_list)} tools from FastMCP registry")
            
            for tool in tools_list:
                logger.info(f"Processing tool: {tool.name}, mode: {mode}")
                # Skip connect_to_jupyter tool when running as Jupyter extension
                # since it doesn't make sense to connect to a different server
                # when already running inside Jupyter
                if tool.name == "connect_to_jupyter":
                    logger.info("Skipping connect_to_jupyter tool in JUPYTER_SERVER mode")
                    continue
                    
                # Add FastMCP tool
                tool_dict = {
                    "name": tool.name,
                    "description": tool.description,
                }
                
                # Extract parameter names from inputSchema
                if hasattr(tool, 'inputSchema') and tool.inputSchema:
                    input_schema = tool.inputSchema
                    if 'properties' in input_schema:
                        tool_dict["parameters"] = list(input_schema['properties'].keys())
                    else:
                        tool_dict["parameters"] = []
                    
                    # Include full inputSchema for MCP protocol compatibility
                    tool_dict["inputSchema"] = input_schema
                else:
                    tool_dict["parameters"] = []
                
                all_tools.append(tool_dict)
            
            logger.info(f"Added {len(all_tools) - len(jupyter_tool_names)} FastMCP tool(s), total: {len(all_tools)}")
            
        except Exception as e:
            logger.error(f"Error retrieving FastMCP tools: {e}", exc_info=True)
        
        return all_tools
    
    # For MCP_SERVER mode, use local FastMCP registry
    # Use FastMCP's list_tools method which returns Tool objects
    tools_list = await mcp.list_tools()
    
    tools = []
    for tool in tools_list:
        tool_dict = {
            "name": tool.name,
            "description": tool.description,
        }
        
        # Extract parameter names from inputSchema
        if hasattr(tool, 'inputSchema') and tool.inputSchema:
            input_schema = tool.inputSchema
            if 'properties' in input_schema:
                tool_dict["parameters"] = list(input_schema['properties'].keys())
            else:
                tool_dict["parameters"] = []
            
            # Include full inputSchema for MCP protocol compatibility
            tool_dict["inputSchema"] = input_schema
        else:
            tool_dict["parameters"] = []
        
        # Include full outputSchema for MCP protocol compatibility
        if hasattr(tool, 'outputSchema') and tool.outputSchema:
            tool_dict["outputSchema"] = tool.outputSchema
        else:
            tool_dict["outputSchema"] = []
        
        tools.append(tool_dict)
    
    return tools
