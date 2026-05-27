# Copyright (c) 2024- Datalayer, Inc.
#
# BSD 3-Clause License

"""Base classes and enums for MCP tools."""

import json
import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Optional

from jupyter_server_client import JupyterServerClient
from jupyter_kernel_client import KernelClient

logger = logging.getLogger(__name__)


class ServerMode(str, Enum):
    """Enum to indicate which server mode the tool is running in."""
    MCP_SERVER = "mcp_server"
    JUPYTER_SERVER = "jupyter_server"


class BaseTool(ABC):
    """Abstract base class for all MCP tools.

    Each tool must implement the execute method which handles both
    MCP_SERVER mode (using HTTP clients) and JUPYTER_SERVER mode
    (using direct API access to serverapp managers).
    """

    def __init__(self):
        """Initialize the tool."""
        pass

    async def _save_notebook_to_disk(self, server_url: str, token: str, path: str) -> None:
        """Trigger a save of the notebook via Jupyter contents API (GET then PUT).

        Forces the server to flush the in-memory Yjs doc state to the .ipynb
        file on disk, preventing cell duplication / reordering on server restart.
        """
        from tornado import httpclient

        base = server_url.rstrip("/")
        encoded_path = path.lstrip("/")
        headers = {"Authorization": f"token {token}", "Content-Type": "application/json"}

        http_client = httpclient.AsyncHTTPClient()
        try:
            get_resp = await http_client.fetch(
                f"{base}/api/contents/{encoded_path}",
                method="GET",
                headers=headers,
                raise_error=True,
            )
            content = json.loads(get_resp.body)
            await http_client.fetch(
                f"{base}/api/contents/{encoded_path}",
                method="PUT",
                headers=headers,
                body=json.dumps(content),
                raise_error=True,
            )
            logger.info(f"Saved notebook {path} to disk via contents API")
        except Exception as e:
            logger.warning(f"Failed to save notebook {path} to disk: {e}")

    async def _save_to_disk(self, notebook_manager: Any, notebook_path: str) -> None:
        """Convenience wrapper: extract connection info from notebook_manager and save.

        No-op when server_url is "local" (JUPYTER_SERVER file-based mode already
        writes directly to disk).
        """
        nb_info = notebook_manager._notebooks.get(notebook_path, {}).get("notebook_info", {})
        server_url = nb_info.get("server_url", "")
        token = nb_info.get("token", "")
        path = nb_info.get("path", notebook_path)
        if server_url and server_url != "local":
            await self._save_notebook_to_disk(server_url, token, path)

    @abstractmethod
    async def execute(
        self,
        mode: ServerMode,
        server_client: Optional[JupyterServerClient] = None,
        kernel_client: Optional[KernelClient] = None,
        contents_manager: Optional[Any] = None,
        kernel_manager: Optional[Any] = None,
        kernel_spec_manager: Optional[Any] = None,
        **kwargs
    ) -> Any:
        """Execute the tool logic."""
        pass
