# Copyright (c) 2024- Datalayer, Inc.
#
# BSD 3-Clause License

"""Tools package for Jupyter MCP Server.

Each tool is implemented as a separate class with an execute method
that can operate in either MCP_SERVER or JUPYTER_SERVER mode.
"""

from jupyter_mcp_server.tools._base import BaseTool, ServerMode

# Import tool implementations - Cell Reading
from jupyter_mcp_server.tools.read_notebook_tool import ReadNotebookTool
from jupyter_mcp_server.tools.read_cell_tool import ReadCellTool

# Import tool implementations - Cell Writing
from jupyter_mcp_server.tools.insert_cell_tool import InsertCellTool
from jupyter_mcp_server.tools.overwrite_cell_source_tool import OverwriteCellSourceTool
from jupyter_mcp_server.tools.edit_cell_source_tool import EditCellSourceTool
from jupyter_mcp_server.tools.delete_cell_tool import DeleteCellTool
from jupyter_mcp_server.tools.move_cell_tool import MoveCellTool

# Import tool implementations - Cell Execution
from jupyter_mcp_server.tools.execute_cell_tool import ExecuteCellTool

# Import tool implementations - Kernel Management
from jupyter_mcp_server.tools.create_kernel_tool import CreateKernelTool
from jupyter_mcp_server.tools.delete_kernel_tool import DeleteKernelTool
from jupyter_mcp_server.tools.restart_kernel_tool import RestartKernelTool
from jupyter_mcp_server.tools.list_kernels_tool import ListKernelsTool
from jupyter_mcp_server.tools.list_kernel_specs_tool import ListKernelSpecsTool
from jupyter_mcp_server.tools.list_notebooks_tool import ListNotebooksTool

# Import tool implementations - Kernel-Notebook Association
from jupyter_mcp_server.tools.attach_kernel_tool import AttachKernelTool
from jupyter_mcp_server.tools.detach_kernel_tool import DetachKernelTool

# Import tool implementations - Other Tools
from jupyter_mcp_server.tools.execute_code_tool import ExecuteCodeTool
from jupyter_mcp_server.tools.list_files_tool import ListFilesTool
from jupyter_mcp_server.tools.connect_jupyter_tool import ConnectJupyterTool

# Import MCP prompt
from jupyter_mcp_server.tools.jupyter_cite_prompt import JupyterCitePrompt

__all__ = [
    "BaseTool",
    "ServerMode",
    # Cell Reading
    "ReadNotebookTool",
    "ReadCellTool",
    # Cell Writing
    "InsertCellTool",
    "OverwriteCellSourceTool",
    "EditCellSourceTool",
    "DeleteCellTool",
    "MoveCellTool",
    # Cell Execution
    "ExecuteCellTool",
    # Kernel Management
    "CreateKernelTool",
    "DeleteKernelTool",
    "RestartKernelTool",
    "ListKernelsTool",
    "ListKernelSpecsTool",
    # Notebook Status
    "ListNotebooksTool",
    # Kernel-Notebook Association
    "AttachKernelTool",
    "DetachKernelTool",
    # Other Tools
    "ExecuteCodeTool",
    "ListFilesTool",
    "ConnectJupyterTool",
    # MCP Prompt
    "JupyterCitePrompt",
]
