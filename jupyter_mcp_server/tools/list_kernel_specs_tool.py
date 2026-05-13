# Copyright (c) 2024- Datalayer, Inc.
#
# BSD 3-Clause License

"""List all available kernel specs tool."""

from typing import Any, Optional, List, Dict
from jupyter_server_client import JupyterServerClient

from jupyter_mcp_server.tools._base import BaseTool, ServerMode
from jupyter_mcp_server.utils import format_TSV


class ListKernelSpecsTool(BaseTool):
    """List all available kernel specs that can be started on the Jupyter server."""

    def _list_specs_http(self, server_client: JupyterServerClient) -> List[Dict[str, str]]:
        """List kernel specs using HTTP API (MCP_SERVER mode)."""
        try:
            kernels_specs = server_client.kernelspecs.list_kernelspecs()
            output = []
            if hasattr(kernels_specs, 'kernelspecs'):
                for name, spec in kernels_specs.kernelspecs.items():
                    entry = {"name": name, "display_name": "unknown", "language": "unknown"}
                    if hasattr(spec, 'spec'):
                        if hasattr(spec.spec, 'display_name'):
                            entry["display_name"] = spec.spec.display_name
                        if hasattr(spec.spec, 'language'):
                            entry["language"] = spec.spec.language or "unknown"
                    output.append(entry)
            return output
        except Exception as e:
            raise RuntimeError(f"Error listing kernel specs via HTTP: {str(e)}")

    async def _list_specs_local(self, kernel_spec_manager: Any) -> List[Dict[str, str]]:
        """List kernel specs using local kernel_spec_manager (JUPYTER_SERVER mode)."""
        try:
            all_specs = await kernel_spec_manager.get_all_specs()
            if all_specs is None:
                return []
            output = []
            for name, spec_info in all_specs.items():
                spec = spec_info.get('spec', {})
                entry = {
                    "name": name,
                    "display_name": spec.get('display_name', 'unknown'),
                    "language": spec.get('language', 'unknown') or "unknown",
                }
                output.append(entry)
            return output
        except Exception as e:
            raise RuntimeError(f"Error listing kernel specs locally: {str(e)}")

    async def execute(
        self,
        mode: ServerMode,
        server_client: Optional[JupyterServerClient] = None,
        kernel_spec_manager: Optional[Any] = None,
        **kwargs
    ) -> str:
        """List all kernel specs available to start.

        Args:
            mode: Server mode (MCP_SERVER or JUPYTER_SERVER)
            server_client: HTTP client for MCP_SERVER mode
            kernel_spec_manager: Kernel spec manager for JUPYTER_SERVER mode
            **kwargs: Additional parameters (unused)

        Returns:
            Tab-separated table with columns: Name, Display_Name, Language
        """
        if mode == ServerMode.JUPYTER_SERVER and kernel_spec_manager is not None:
            specs = await self._list_specs_local(kernel_spec_manager)
        elif mode == ServerMode.MCP_SERVER and server_client is not None:
            specs = self._list_specs_http(server_client)
        else:
            raise ValueError(f"Invalid mode or missing required managers/clients: mode={mode}")

        if not specs:
            return "No kernel specs found on the Jupyter server."

        headers = ["Name", "Display_Name", "Language"]
        rows = [[s["name"], s["display_name"], s["language"]] for s in specs]
        return format_TSV(headers, rows)
