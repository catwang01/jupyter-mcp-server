<!--
  ~ Copyright (c) 2024- Datalayer, Inc.
  ~
  ~ BSD 3-Clause License
-->

# Jupyter MCP Server - Architecture

## Overview

The Jupyter MCP Server supports **dual-mode operation**:

1. **MCP_SERVER Mode** (Standalone) - Connects to remote Jupyter servers via HTTP/WebSocket
2. **JUPYTER_SERVER Mode** (Extension) - Runs embedded in Jupyter Server with direct API access

Both modes share the same tool implementations, with automatic backend selection based on configuration.

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                       MCP Client                                │
│            (Claude Desktop, VS Code, Cursor, etc.)              │
└────────────┬────────────────────────────────────┬───────────────┘
             │                                    │
             │ stdio/SSE                          │ HTTP/SSE
             │                                    │
    ┌────────▼────────────┐          ┌───────────▼──────────────┐
    │   MCP_SERVER Mode   │          │  JUPYTER_SERVER Mode     │
    │   (Standalone)      │          │  (Extension)             │
    │                     │          │                          │
    │   CLI Layer         │          │    Extension Handlers    │
    │  (CLI.py)           │          │  (handlers.py)           │
    └──────────┬──────────┘          └──────────┬───────────────┘
               │                                │
               │ Configuration                  │ Configuration
               │                                │
    ┌──────────▼──────────┐          ┌──────────▼──────────────┐
    │   Server Layer      │          │   Extension Context     │
    │  (server.py)        │          │  (context.py)           │
    │                     │          │                         │
    │  - FastMCP Server   │          │  - ServerApp Access     │
    │  - Tool Wrappers    │          │  - Manager Access       │
    │  - Error Handling   │          │  - Backend Selection    │
    └──────────┬──────────┘          └──────────┬──────────────┘
               │                                │
               │ Tool Delegation                │ Tool Delegation
               │                                │
        ┌──────▼────────────────────────────────▼──────┐
        │          Tool Implementation Layer           │
        │         (jupyter_mcp_server/tools/)          │
        │                                              │
        │  19 Tools in 4 Categories:                   │
        │  • Server Management (3)                     │
        │  • Kernel Management (4+2 association)       │
        │  • Notebook Reading (3)                      │
        │  • Cell Operations (7+1 combined)            │
        │                                              │
        │  Each tool implements:                       │
        │  - Dual-mode execution logic                 │
        │  - Backend abstraction                       │
        │  - Error handling and recovery               │
        └──────────┬───────────────────────┬───────────┘
                   │                       │
                   │ Mode Selection        │ Backend Selection
                   │                       │
          ┌────────▼────────┐     ┌────────▼────────┐
          │ Remote Backend  │     │  Local Backend  │
          │                 │     │                 │
          │ - HTTP Clients  │     │ - Direct API    │
          │ - WebSocket     │     │ - Zero Overhead │
          │ - Client Libs   │     │ - YDoc Support  │
          └────────┬────────┘     └────────┬────────┘
                   │                       │
                   │ HTTP/WS               │ Direct Python API
                   │                       │
            ┌──────▼──────────┐    ┌───────▼────────┐
            │ Remote Jupyter  │    │ Local Jupyter  │
            │ Server          │    │ Server         │
            └─────────────────┘    └────────────────┘
```

## Core Components

### 1. CLI Layer (`CLI.py`)

**Command-Line Interface** - Primary entry point for users and MCP clients:

**Key Features**:
- **Configuration Management**: Handles all startup configuration via command-line options and environment variables
- **Transport Selection**: Supports both `stdio` (for direct MCP client integration) and `streamable-http` (for HTTP-based clients)
- **Auto-Enrollment**: Automatically connects to specified notebooks on startup
- **Provider Support**: Supports both `jupyter` and `datalayer` providers
- **URL Resolution**: Intelligent URL and token resolution with fallback mechanisms

**Integration**:
- Calls `server.py` functions to initialize the MCP server
- Passes configuration to `ServerContext` for mode detection
- Handles kernel startup and notebook enrollment lifecycle

### 2. Backend Layer (`jupyter_mcp_server/jupyter_extension/backends/`)

**Backend Abstraction** - Unified interface for notebook and kernel operations:

**LocalBackend** - Complete implementation using local Jupyter Server APIs:
- Uses `serverapp.contents_manager` for file operations
- Uses `serverapp.kernel_manager` for kernel operations
- Direct Python API calls with minimal overhead
- Supports both file-based and YDoc collaborative editing

**RemoteBackend** - Placeholder implementation for HTTP/WebSocket access:
- Designed for `jupyter_server_client`, `jupyter_kernel_client`, `jupyter_nbmodel_client`
- Maintains 100% backward compatibility with existing MCP_SERVER mode
- Currently marked as "Not Implemented" - to be refactored from server.py

### 3. Server Context Layer

**Multiple Context Managers**:

**MCP Server Context** (`server_context.py::ServerContext`):
- Singleton managing server mode for standalone MCP_SERVER mode
- Provides HTTP clients for remote Jupyter server access
- Mode detection based on configuration

**Extension Context** (`jupyter_extension/context.py::ServerContext`):
- Singleton managing server mode for JUPYTER_SERVER extension mode
- Provides direct access to serverapp managers (contents_manager, kernel_manager)
- Handles configuration from Jupyter extension traits

**Mode Detection**:
- **JUPYTER_SERVER**: When running as extension, serverapp available
- **MCP_SERVER**: When running standalone, connects via HTTP

### 4. FastMCP Server Layer (`server.py`)

**FastMCP Integration** - Core MCP protocol implementation:

```python
# Global MCP server instance with CORS support
mcp = FastMCPWithCORS(name="Jupyter MCP Server", json_response=False, stateless_http=True)
notebook_manager = NotebookManager()
server_context = ServerContext.get_instance()

# Tool registration and execution
@mcp.tool()
async def list_files(path: str = "", max_depth: int = 1, ...) -> str:
    """List files and directories in Jupyter server filesystem"""
    return await safe_notebook_operation(
        lambda: ListFilesTool().execute(
            mode=server_context.mode,
            server_client=server_context.server_client,
            contents_manager=server_context.contents_manager,
            path=path,
            max_depth=max_depth,
            ...
        )
    )

# Kernel-notebook association: explicit attach/detach
@mcp.tool()
async def attach_kernel(notebook_path: str, kernel_id: str) -> str:
    """Associate a kernel with a notebook path, enabling cell execution."""
    return await safe_notebook_operation(
        lambda: AttachKernelTool().execute(
            mode=server_context.mode,
            notebook_manager=notebook_manager,
            notebook_path=notebook_path,
            kernel_id=kernel_id,
            ...
        )
    )
```

**Key Responsibilities**:
- **Tool Registration**: All 19 MCP tools are registered as FastMCP decorators
- **Mode Detection**: Automatically detects and initializes appropriate server mode
- **Error Handling**: Provides `safe_notebook_operation()` wrapper with retry logic
- **Resource Management**: Manages notebook connections and kernel lifecycle
- **Protocol Bridge**: Translates between MCP protocol and internal tool implementations

**Transport Support**:
- **stdio**: Direct communication with MCP clients via standard input/output
- **streamable-http**: HTTP-based communication with SSE (Server-Sent Events) support
- **CORS Middleware**: Enables cross-origin requests for web-based MCP clients

### 5. Tool Implementation Layer (`jupyter_mcp_server/tools/`)

**Built-in Tool Implementations** - Complete set of Jupyter operations:

**Important: Gateway-Aware Async Handling**

When Jupyter is configured with `--GatewayClient.url`, key manager methods become async:
- `GatewayKernelSpecManager.get_all_specs()` — async (base `KernelSpecManager` is sync)
- `GatewayMappingKernelManager.list_kernels()` — async (base `MappingKernelManager` is sync)

All tool methods that call these must:
1. Be `async def` themselves
2. Use `await` on these calls
3. Guard against `None` return values (gateway may return missing keys)

This applies to `ListKernelSpecsTool._list_specs_local()` and
`ListKernelsTool._list_kernels_local()`. Do **not** attempt to run these calls in a
separate thread — the gateway HTTP client depends on tornado's main event loop.

```python
# Tool Categories and Examples

# Server Management (3 tools)
class ListFilesTool(BaseTool):        # File system exploration
class ListKernelsTool(BaseTool):      # List running kernels
class ConnectJupyterTool(BaseTool):   # Dynamic server connection

# Kernel Management (4 tools)
class CreateKernelTool(BaseTool):     # Create standalone kernel
class DeleteKernelTool(BaseTool):     # Stop and delete kernel
class RestartKernelTool(BaseTool):    # Restart kernel
class ListKernelSpecsTool(BaseTool):  # List available kernel types

# Kernel-Notebook Association (2 tools)
class AttachKernelTool(BaseTool):     # Link kernel to notebook
class DetachKernelTool(BaseTool):     # Unlink kernel from notebook

# Notebook Reading (2 tools)
class ReadNotebookTool(BaseTool):     # Read notebook cells
class ReadCellTool(BaseTool):         # Read individual cell

# Notebook Status (1 tool)
class ListNotebooksTool(BaseTool):    # List notebook-kernel attachments

# Cell Writing (5 tools)
class InsertCellTool(BaseTool):       # Insert new cells
class DeleteCellTool(BaseTool):       # Delete cells
class OverwriteCellSourceTool(BaseTool): # Replace entire cell source
class EditCellSourceTool(BaseTool):   # Surgical find-and-replace
class MoveCellTool(BaseTool):         # Move cell position

# Cell Execution (2 tools + 1 combined)
class ExecuteCellTool(BaseTool):      # Execute cell in notebook
class ExecuteCodeTool(BaseTool):      # Execute code directly in kernel
# insert_execute_code_cell: Combined insert+execute (inline in server.py)
```

**Implementation Architecture**:
- **BaseTool Abstract Class**: Defines `execute()` method signature with dual-mode support
- **ServerMode Enum**: Distinguishes between `MCP_SERVER` and `JUPYTER_SERVER` modes
- **Dual-Mode Logic**: Each tool implements both local and remote execution paths
- **Backend Integration**: Tools automatically select appropriate backend based on mode

**Tool Categories**:
1. **Server Management**: File system, kernel introspection, and dynamic connection
2. **Kernel Management**: Kernel lifecycle (create, delete, restart, list specs)
3. **Kernel-Notebook Association**: Explicit linking of kernels to notebooks
4. **Notebook Status**: List notebook-kernel attachment state
5. **Cell Operations**: Fine-grained cell manipulation and execution

**Cell ID Addressing**:

All cell-locating tools accept an optional `cell_id` parameter (nbformat 4.5 stable cell ID) as an alternative to positional `cell_index`. When both are provided, `cell_id` takes priority. Resolution is handled by a shared utility:

```python
# utils.py
def resolve_cell_index(cells, *, cell_id=None, cell_index=None) -> int:
    """Resolve cell_id or cell_index to a concrete integer index.
    Priority: cell_id > cell_index.
    Works with dict-like cells (Y.js, WebSocket) and object cells (nbformat)."""
```

Each tool calls `resolve_cell_index()` after loading cells but before bounds checking. Tool-specific variations:
- `insert_cell` / `insert_execute_code_cell`: Additional `insert_position: Literal["before", "after"]` controls placement relative to the referenced cell
- `delete_cell`: Accepts `cell_ids: list[str]` which is merged (union) with `cell_indices`
- `move_cell`: Accepts `source_cell_id` and `target_cell_id` independently (can mix with index params)

`read_notebook` and `read_cell` output includes cell IDs so downstream tools can discover and reference them.

**Output Format (TSV)**:

All listing tools (`list_kernels`, `list_kernel_specs`, `list_notebooks`, `list_files`, `read_notebook`) return tab-separated values (TSV) via `format_TSV(headers, rows)` from `utils.py`. Design rationale:
- MCP tool responses are consumed by LLMs, not parsed by programs
- TSV is ~40% more token-efficient than equivalent JSON (no braces, quotes, commas)
- LLMs read tabular TSV as easily as JSON arrays
- The MCP protocol already provides structure (tool name, content type); content layer doesn't need another JSON wrapper

When a listing tool has no results, it returns a human-readable message (e.g., "No kernels found on the Jupyter server.") instead of an empty table.

**Error Output Format**:

Execution tools return errors as `[ERROR: TypeName: message]` strings (not exceptions). The format always includes the exception class name so that errors like `KeyError` (whose `str()` omits the class name) remain diagnosable. Pattern:
```python
return [f"[ERROR: {type(e).__name__}: {e}]"]
```
This applies to all `except Exception as e` handlers in `utils.py` (`execute_via_execution_stack`, `execute_code_local`, `execute_cell_local`) and in tool files (`execute_code_tool.py`, `execute_cell_tool.py`).

**Dynamic Tool Registry** (`get_registered_tools()`):
- Queries FastMCP's `list_tools()` to get all registered tools
- Returns tool metadata (name, description, parameters, inputSchema)
- Used by Jupyter extension to expose tools without hardcoding
- Supports both FastMCP tools and jupyter-mcp-tools integration

### 6. Jupyter Extension Layer (`jupyter_extension/`)

**Extension App** (`extension.py::JupyterMCPServerExtensionApp`):
```python
class JupyterMCPServerExtensionApp(ExtensionApp):
    name = "jupyter_mcp_server"
    
    # Configuration traits
    document_url = Unicode("local", config=True)
    runtime_url = Unicode("local", config=True)
    document_id = Unicode("notebook.ipynb", config=True)
    
    def initialize_settings(self):
        # Store config in Tornado settings
        # Initialize ServerContext with JUPYTER_SERVER mode
```

**Handlers** (`handlers.py`):
- `MCPHealthHandler`: GET /mcp/healthz
- `MCPToolsListHandler`: GET /mcp/tools/list (uses `get_registered_tools()`)
- `MCPToolsCallHandler`: POST /mcp/tools/call
- `MCPSSEHandler`: SSE endpoint for MCP protocol

**Extension Context** (`context.py::ServerContext`):
```python
class ServerContext:
    _serverapp: Optional[Any] = None
    _context_type: str = "unknown"
    
    def update(self, context_type: str, serverapp: Any):
        """Called by extension to register serverapp."""
    
    def is_local_document(self) -> bool:
        """Check if document operations use local access."""
    
    def get_contents_manager(self):
        """Get local contents_manager from serverapp."""
```

### 7. Notebook Manager (`notebook_manager.py`)

**Purpose**: Manages kernel client registry and kernel-notebook attachments.

**Architecture**: Notebooks (files) and Kernels (processes) are managed independently.
Use `attach_kernel` / `detach_kernel` to associate them explicitly.

**Key Features**:
- Kernel client registry (kernel_id → KernelClient or metadata dict)
- Attachment registry (notebook_path → kernel_id)
- `NotebookConnection` context manager for Y.js document access (MCP_SERVER mode)
- Legacy default kernel support for `/api/connect` route

```python
class NotebookManager:
    # Kernel client registry
    def add_kernel_client(self, kernel_id, client): ...
    def get_kernel_client(self, kernel_id): ...
    def remove_kernel_client(self, kernel_id): ...
    def list_kernel_clients(self): ...

    # Attachment registry (notebook_path ↔ kernel_id)
    def attach(self, notebook_path, kernel_id): ...
    def detach(self, notebook_path): ...
    def detach_by_kernel(self, kernel_id): ...
    def get_kernel_id(self, notebook_path): ...
    def list_attachments(self): ...

    # Notebook WebSocket connection (MCP_SERVER mode)
    def get_notebook_connection(self, notebook_path): ...

    # Legacy helpers for /api/connect route
    def set_default_kernel(self, kernel_id, client): ...
    def get_default_kernel(self): ...
    def clear_default_kernel(self): ...
```

### 8. Hook System (`hooks.py`, `otel_hook.py`)

**Pre/Post Hook System** - Observability and extensibility layer:

```python
class HookEvent(str, Enum):
    BEFORE_TOOL_CALL = "before_tool_call"
    AFTER_TOOL_CALL = "after_tool_call"
    BEFORE_EXECUTE = "before_execute"
    AFTER_EXECUTE = "after_execute"
    KERNEL_LIFECYCLE = "kernel_lifecycle"
```

**Key Components**:
- **HookRegistry**: Singleton that manages handler registration and event dispatch
- **HookHandler Protocol**: Interface for custom handlers with `propagate_errors` flag
- **`@with_hooks` Decorator**: Applied to all tool functions in `server.py` to fire `BEFORE_TOOL_CALL` / `AFTER_TOOL_CALL` events
- **Context Correlation**: Before/after event pairs share a context dict for handler state

**Built-in OTel Handler** (`otel_hook.py`):
- Emits OpenTelemetry spans for tool calls, code execution, and kernel lifecycle
- Uses `FileSpanExporter` to write spans as JSONL
- Activated via `--otel-file` CLI arg, `JUPYTER_MCP_OTEL_FILE` env var, or Jupyter traitlet
- Non-propagating (`propagate_errors = False`) — never disrupts tool execution

**Integration Points**:
- `server.py`: All tools decorated with `@with_hooks`; kernel lifecycle events fired on use/restart/unuse
- `execute_cell_tool.py`, `execute_code_tool.py`: Fire `BEFORE_EXECUTE` / `AFTER_EXECUTE` around kernel execution
- `utils.py`: Fires execution hooks in shared utility functions

## Configuration

### MCP_SERVER Mode (Standalone)

**Start Command**:
```bash
jupyter-mcp-server start \
  --transport streamable-http \
  --document-url http://localhost:8888 \
  --runtime-url http://localhost:8888 \
  --document-token MY_TOKEN \
  --runtime-token MY_TOKEN \
  --port 4040
```

**Behavior**:
- ServerContext initialized with `mode=ServerMode.MCP_SERVER`
- Tools use HTTP clients for remote Jupyter server access
- Notebook connections use `NbModelClient` for WebSocket (Y.js documents)
- Uses RemoteBackend (placeholder implementation)

### JUPYTER_SERVER Mode (Extension)

**Start Command**:
```bash
jupyter server \
  --JupyterMCPServerExtensionApp.document_url=local \
  --JupyterMCPServerExtensionApp.runtime_url=local \
  --JupyterMCPServerExtensionApp.document_id=notebook.ipynb
```

**Configuration File** (`jupyter_server_config.py`):
```python
c.ServerApp.jpserver_extensions = {"jupyter_mcp_server": True}
c.JupyterMCPServerExtensionApp.document_url = "local"
c.JupyterMCPServerExtensionApp.runtime_url = "local"
```

**Backend Selection**:
- **LocalBackend**: Used when `document_url="local"` or `runtime_url="local"`
  - Direct access to `serverapp.contents_manager`, `serverapp.kernel_manager`
  - No network overhead, maximum performance
  - Supports both file-based and YDoc collaborative editing
- **RemoteBackend**: Used when connecting to remote Jupyter servers
  - HTTP/WebSocket access via client libraries
  - Placeholder implementation (to be completed)

**Behavior**:
- Extension auto-enabled (via `jupyter-config/` file)
- ServerContext updated with `mode=ServerMode.JUPYTER_SERVER`
- Tools automatically select LocalBackend for optimal performance
- Cell reading tools parse notebook JSON from file system or YDoc

## Request Flow Examples

### Example 1: Create and Attach Kernel

```
MCP Client
  → create_kernel_tool(kernel_name="python3")
    → CreateKernelTool().execute(mode=JUPYTER_SERVER, ...)
      → kernel_manager.start_kernel()
      → notebook_manager.add_kernel_client(kernel_id, client)
    ← "Kernel created successfully.\nID: <id>\nName: python3"

  → attach_kernel(notebook_path="my_notebook.ipynb", kernel_id="<id>")
    → AttachKernelTool().execute(...)
      → notebook_manager.attach("my_notebook.ipynb", "<id>")
    ← "Attached kernel <id> to my_notebook.ipynb"
```

### Example 2: Read Cell (JUPYTER_SERVER Mode with LocalBackend)

```
MCP Client
  → POST /mcp/tools/call {"tool_name": "read_cell", "arguments": {"notebook_path": "nb.ipynb", "cell_index": 0}}
    → MCPSSEHandler (or MCPToolsCallHandler)
      → FastMCP calls @mcp.tool() wrapper
        → ReadCellTool().execute(
            mode=JUPYTER_SERVER,
            contents_manager=serverapp.contents_manager,
            notebook_manager=notebook_manager
          )
          → LocalBackend.get_notebook_content(notebook_path)
            → contents_manager.get(notebook_path, content=True, type='notebook')
              → Direct file system access (no HTTP)
            ← Notebook JSON content
          → Parse cells and format response
          ← Cell information with metadata and source
        ← Tool result
      ← JSON-RPC response
    ← SSE message
  ← Cell content displayed
```

### Example 3: Execute Cell (MCP_SERVER Mode with RemoteBackend)

```
MCP Client
  → execute_cell(notebook_path="nb.ipynb", cell_index=0)
    → ExecuteCellTool().execute(mode=MCP_SERVER, ...)
      → notebook_manager.get_kernel_id("nb.ipynb")  → kernel_id
      → notebook_manager.get_notebook_connection("nb.ipynb")
        → NbModelClient establishes WebSocket to Y.js document
      → Execute code via kernel connection
        → HTTP/WebSocket to remote kernel
        → Real-time execution with progress updates
      ← Execution outputs with rich formatting
    ← Tool result
  ← Outputs displayed
```

## Tool Registration Flow

```
1. CLI startup (CLI.py)
   ↓
2. Configuration parsing and validation
   ↓
3. ServerContext initialization with mode detection
   ↓
4. FastMCP server initialization (server.py)
   ↓
5. Tool instance creation (19 tool implementations)
   ↓
6. @mcp.tool() wrapper registration
   ↓
7. FastMCP internal tool registry
   ↓
8. Dynamic tool discovery via get_registered_tools()
   ↓
9. Extension handlers expose tools via /mcp/tools/list
   ↓
10. MCP clients discover and invoke tools
```

## File Structure

```
jupyter_mcp_server/
├── __init__.py                 # Package initialization
├── __main__.py                 # Module entry point (imports CLI)
├── __version__.py              # Version information
│
├── CLI.py                      # 🏠 Command-Line Interface (Primary Entry Point)
│   ├── Command parsing and validation
│   ├── Environment variable handling
│   ├── Transport selection (stdio/streamable-http)
│   ├── Provider support (jupyter/datalayer)
│   ├── Auto-enrollment of notebooks
│   └── Server lifecycle management
│
├── server.py                   # 🔧 FastMCP Server Layer
│   ├── MCP protocol implementation
│   # Tool registration (19 @mcp.tool decorators)
│   ├── Error handling with safe_notebook_operation()
│   ├── Resource management and cleanup
│   ├── Dynamic tool registry (get_registered_tools())
│   └── Transport support (stdio + streamable-http)
│
├── tools/                      # 🛠️ Built-in Tool Implementations
│   ├── __init__.py            # Exports BaseTool, ServerMode, all tool classes
│   ├── _base.py               # Abstract base class for all tools
│   │
│   # Server Management Tools (3)
│   ├── list_files_tool.py     # File system exploration
│   ├── list_kernels_tool.py   # List running kernels
│   ├── connect_jupyter_tool.py # Dynamic server connection
│   │
│   # Kernel Management Tools (4)
│   ├── create_kernel_tool.py  # Create standalone kernel
│   ├── delete_kernel_tool.py  # Stop and delete kernel
│   ├── restart_kernel_tool.py # Restart kernel
│   ├── list_kernel_specs_tool.py # List available kernel types
│   │
│   # Kernel-Notebook Association Tools (2)
│   ├── attach_kernel_tool.py  # Link kernel to notebook
│   ├── detach_kernel_tool.py  # Unlink kernel from notebook
│   │
│   # Notebook Reading Tools (2)
│   ├── read_notebook_tool.py  # Read notebook cells
│   ├── read_cell_tool.py      # Read individual cells
│   │
│   # Notebook Status Tools (1)
│   ├── list_notebooks_tool.py # List notebook-kernel attachments
│   │
│   # Cell Writing Tools (5)
│   ├── insert_cell_tool.py    # Insert new cells
│   ├── delete_cell_tool.py    # Delete cells
│   ├── overwrite_cell_source_tool.py # Replace entire cell source
│   ├── edit_cell_source_tool.py # Surgical find-and-replace
│   ├── move_cell_tool.py      # Move cell position
│   │
│   # Cell Execution Tools (2 + insert_execute_code_cell inline in server.py)
│   ├── execute_cell_tool.py   # Execute cell in notebook
│   ├── execute_code_tool.py   # Execute code directly in kernel
│   │
│   └── jupyter_cite_prompt.py # MCP prompt resource
│
├── config.py                   # ⚙️ Configuration Management
│   ├── Singleton config object (JupyterMCPConfig)
│   ├── Environment variable parsing
│   ├── URL and token resolution
│   └── Provider-specific settings
│
├── notebook_manager.py         # 📚 Kernel & Attachment Management
│   ├── Kernel client registry (kernel_id → client)
│   ├── Attachment registry (notebook_path → kernel_id)
│   ├── NotebookConnection context manager (WebSocket/Y.js)
│   └── Legacy default kernel helpers
│
├── server_context.py           # 🎯 Server Context (MCP_SERVER mode)
│   ├── Mode detection and initialization
│   ├── HTTP client management
│   └── Configuration state management
│
├── utils.py                    # 🧰 Utility Functions
│   ├── Cell ID resolution (resolve_cell_index)
│   ├── Execution utilities (local/remote)
│   ├── Output processing and formatting
│   ├── Kernel management helpers
│   └── YDoc integration support
│
├── hooks.py                    # 🔍 Hook System
│   ├── HookEvent enum (5 event types)
│   ├── HookHandler protocol
│   ├── HookRegistry singleton
│   └── @with_hooks decorator
│
├── otel_hook.py                # 📡 OpenTelemetry Integration
│   ├── OTelHookHandler (span emission)
│   ├── FileSpanExporter (JSONL output)
│   └── maybe_register_otel() auto-setup
│
├── enroll.py                   # 🔗 Auto-Enrollment System
│   ├── Automatic kernel creation + notebook attachment
│   ├── Supports existing kernel (runtime_id) or new kernel
│   └── Configuration-based initialization
│
├── models.py                   # 📋 Data Models
│   ├── Pydantic models for API
│   ├── Cell and Notebook structures
│   └── Configuration validation
│
└── jupyter_extension/          # 🔌 Jupyter Server Extension
    ├── extension.py           # Jupyter extension app
    ├── handlers.py            # HTTP request handlers
    ├── context.py             # Extension context manager
    ├── backends/              # Backend implementations
    │   ├── base.py            # Backend interface
    │   ├── local_backend.py   # Local API (Complete)
    │   └── remote_backend.py  # Remote API (Placeholder)
    └── protocol/              # Protocol implementation
        └── messages.py        # MCP message models
```

## Known Issues (JUPYTER_SERVER Mode + JRK Remote Kernels)

When using JRK (jupyter-remote-kernel) via GatewayClient, a chain of failures can produce opaque `[ERROR: AssertionError: ]` responses:

**Error Chain:**
```
JRK remote kernel WebSocket disconnect
  → GatewayKernelClient._route_responses() thread exits
  → channel_queue.response_router_finished = True
  → iopub_channel.get_msg() raises RuntimeError("Response router had finished")
  → jupyter_remote_kernel converts to TimeoutError("Timeout waiting for kernel output")
  → jupyter_server_nbmodel kernel_worker: except BaseException catches TimeoutError
    → Logs "Failed to process execution request" but does NOT set results[uid]
  → execute_via_execution_stack polling loop sees NO_RESULT until our own timeout fires
```

**Bug 1: Missing `await` on `execution_stack.cancel()`** (`utils.py:588`):
```python
# Current (broken): coroutine created but never awaited → no cleanup
execution_stack.cancel(kernel_id)
# Should be:
await execution_stack.cancel(kernel_id)
```
Without cleanup, `__workers`/`__kernel_clients` retain the dead worker and broken WebSocket. Subsequent `put()` calls reuse the dead state.

**Bug 2: `kernel_worker` silent result loss** (upstream `jupyter_server_nbmodel/actions.py:291`):
```python
except (asyncio.CancelledError, KeyboardInterrupt, RuntimeError) as e:
    results[uid] = {"error": str(e)}  # Only RuntimeError sets result
    break
except BaseException as e:  # TimeoutError lands here
    logger.error(...)        # Logged but results[uid] never set → NO_RESULT forever
```

**Bug 3: `AssertionError` from stale GatewayKernelClient** (upstream `jupyter_server/gateway/managers.py`):
After the response router finishes, if `stop_channels()` is called on a client whose `channel_socket` was never initialized (or set to None), bare `assert self.channel_socket is not None` fires with no message → `[ERROR: AssertionError: ]`.

## References

- [MCP Specification](https://modelcontextprotocol.io/specification)
- [Jupyter Server Extension Guide](https://jupyter-server.readthedocs.io/en/latest/developers/extensions.html)
- [FastMCP Documentation](https://github.com/jlowin/fastmcp)
- [Y.js Collaborative Editing](https://github.com/yjs/yjs)

---

**Version**: 0.3.0
**Last Updated**: May 2026
**Status**: Refactored architecture with independent kernel/notebook management and explicit attachment model
