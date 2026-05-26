# Copyright (c) 2024- Datalayer, Inc.
#
# BSD 3-Clause License

"""List all available kernels tool."""

import asyncio
import inspect
from typing import Any, Optional, Dict
from jupyter_server_client import JupyterServerClient

from jupyter_mcp_server.tools._base import BaseTool, ServerMode


class ListKernelsTool(BaseTool):
    """List all available kernels and kernel specs in the Jupyter server."""

    @staticmethod
    def _format_last_activity(value: Any) -> str:
        if not value:
            return "unknown"
        if hasattr(value, 'strftime'):
            return value.strftime("%Y-%m-%d %H:%M:%S")
        return str(value)

    @staticmethod
    def _format_env(env_dict: dict) -> str:
        if not env_dict:
            return "-"
        s = "; ".join(f"{k}={v}" for k, v in env_dict.items())
        return s[:100] + "..." if len(s) > 100 else s

    @staticmethod
    def _empty_spec_entry() -> Dict[str, Any]:
        return {"display_name": "unknown", "language": "unknown", "env": "-", "running_kernels": []}

    @staticmethod
    def _format_output(specs: Dict[str, Dict]) -> str:
        """Render specs grouped with their running instances indented below."""
        lines = []
        for name, info in specs.items():
            lines.append(
                f"[{name}]  {info['display_name']}  ({info['language']})"
                + (f"  env: {info['env']}" if info['env'] != "-" else "")
            )
            if info["running_kernels"]:
                for k in info["running_kernels"]:
                    lines.append(
                        f"    id: {k['id']}  "
                        f"state: {k['state']}  "
                        f"connections: {k['connections']}  "
                        f"last_activity: {k['last_activity']}"
                    )
            else:
                lines.append("    (not running)")
            lines.append("")
        return "\n".join(lines).rstrip()

    @staticmethod
    def _resolve_state(kernel_obj: Any) -> str:
        """Extract execution state from kernel object (HTTP API may use different attr names)."""
        if hasattr(kernel_obj, 'execution_state'):
            return kernel_obj.execution_state
        if hasattr(kernel_obj, 'state'):
            return kernel_obj.state
        return "unknown"

    def _build_specs_http(self, server_client: JupyterServerClient) -> Dict[str, Dict]:
        """Build spec-keyed dict using HTTP API (MCP_SERVER mode)."""
        try:
            kernels_specs = server_client.kernelspecs.list_kernelspecs()
            specs: Dict[str, Dict] = {}
            if hasattr(kernels_specs, 'kernelspecs'):
                for name, spec in kernels_specs.kernelspecs.items():
                    entry = self._empty_spec_entry()
                    if hasattr(spec, 'spec'):
                        if hasattr(spec.spec, 'display_name'):
                            entry["display_name"] = spec.spec.display_name
                        if hasattr(spec.spec, 'language'):
                            entry["language"] = spec.spec.language or "unknown"
                        if hasattr(spec.spec, 'env'):
                            entry["env"] = self._format_env(spec.spec.env or {})
                    specs[name] = entry

            kernels = server_client.kernels.list_kernels() or []
            for kernel in kernels:
                kernel_name = kernel.name or "unknown"
                if kernel_name not in specs:
                    specs[kernel_name] = self._empty_spec_entry()
                specs[kernel_name]["running_kernels"].append({
                    "id": kernel.id or "unknown",
                    "state": self._resolve_state(kernel),
                    "connections": str(kernel.connections) if hasattr(kernel, 'connections') else "unknown",
                    "last_activity": self._format_last_activity(
                        getattr(kernel, 'last_activity', None)
                    ),
                })

            return specs
        except Exception as e:
            raise RuntimeError(f"Error listing kernels via HTTP: {str(e)}")

    async def _build_specs_local(
        self,
        kernel_manager: Any,
        kernel_spec_manager: Any,
    ) -> Dict[str, Dict]:
        """Build spec-keyed dict using local managers (JUPYTER_SERVER mode)."""
        try:
            # Fetch specs and kernels concurrently when both are async
            specs_coro = kernel_spec_manager.get_all_specs() if kernel_spec_manager else {}
            kernels_coro = kernel_manager.list_kernels()

            if inspect.isawaitable(specs_coro) and inspect.isawaitable(kernels_coro):
                all_specs, kernel_infos = await asyncio.gather(specs_coro, kernels_coro)
            else:
                if inspect.isawaitable(specs_coro):
                    all_specs = await specs_coro
                else:
                    all_specs = specs_coro
                if inspect.isawaitable(kernels_coro):
                    kernel_infos = await kernels_coro
                else:
                    kernel_infos = kernels_coro

            all_specs = all_specs or {}

            specs: Dict[str, Dict] = {}
            for name, spec_info in all_specs.items():
                spec = spec_info.get('spec', {})
                entry = self._empty_spec_entry()
                entry["display_name"] = spec.get('display_name', 'unknown')
                entry["language"] = spec.get('language', 'unknown') or 'unknown'
                entry["env"] = self._format_env(spec.get('env') or {})
                specs[name] = entry

            for k in list(kernel_infos):
                kernel_name = k.get('name', 'unknown')
                if kernel_name not in specs:
                    specs[kernel_name] = self._empty_spec_entry()
                specs[kernel_name]["running_kernels"].append({
                    "id": k.get('id', 'unknown'),
                    "state": k.get('execution_state', 'unknown'),
                    "connections": str(k.get('connections', 'unknown')),
                    "last_activity": self._format_last_activity(k.get('last_activity')),
                })

            return specs
        except Exception as e:
            raise RuntimeError(f"Error listing kernels locally: {str(e)}")

    async def execute(
        self,
        mode: ServerMode,
        server_client: Optional[JupyterServerClient] = None,
        kernel_client: Optional[Any] = None,
        contents_manager: Optional[Any] = None,
        kernel_manager: Optional[Any] = None,
        kernel_spec_manager: Optional[Any] = None,
        **kwargs
    ) -> str:
        """List all kernel specs and their running instances.

        Output is two-level:
          [spec_name]  Display Name  (language)
              id: <id>  state: <state>  connections: <n>  last_activity: <t>
              (not running)   ← when no instances are active

        Args:
            mode: Server mode (MCP_SERVER or JUPYTER_SERVER)
            server_client: HTTP client for MCP_SERVER mode
            kernel_manager: Direct kernel manager access for JUPYTER_SERVER mode
            kernel_spec_manager: Kernel spec manager for JUPYTER_SERVER mode
        """
        if mode == ServerMode.JUPYTER_SERVER and kernel_manager is not None:
            specs = await self._build_specs_local(kernel_manager, kernel_spec_manager)
        elif mode == ServerMode.MCP_SERVER and server_client is not None:
            specs = self._build_specs_http(server_client)
        else:
            raise ValueError(f"Invalid mode or missing required managers/clients: mode={mode}")

        if not specs:
            return "No kernel specs found on the Jupyter server."

        return self._format_output(specs)
