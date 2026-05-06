# Copyright (c) 2024- Datalayer, Inc.
#
# BSD 3-Clause License

"""
Notebook and Kernel Management Module

Notebooks (files) and Kernels (processes) are managed independently.
Use attach_kernel / detach_kernel to associate them explicitly.
"""

from typing import Dict, Any, Optional, List
from types import TracebackType

from jupyter_nbmodel_client import NbModelClient, get_notebook_websocket_url

from .config import get_config


class NotebookConnection:
    """
    Context manager for NbModelClient WebSocket connections (MCP_SERVER mode only).
    """

    def __init__(self, notebook_path: str):
        self.notebook_path = notebook_path
        self._notebook: Optional[NbModelClient] = None

    async def __aenter__(self) -> NbModelClient:
        config = get_config()
        ws_url = get_notebook_websocket_url(
            server_url=config.document_url,
            token=config.document_token,
            path=self.notebook_path,
            provider=config.provider,
        )
        self._notebook = NbModelClient(ws_url)
        await self._notebook.__aenter__()
        return self._notebook

    async def __aexit__(
        self,
        exc_type: Optional[type],
        exc_val: Optional[BaseException],
        exc_tb: Optional[TracebackType],
    ) -> None:
        if self._notebook:
            await self._notebook.__aexit__(exc_type, exc_val, exc_tb)


class NotebookManager:
    """
    Manages two independent concerns:

    1. Kernel clients  — kernel_id → KernelClient (MCP_SERVER mode)
                         kernel_id → {"id": kernel_id} dict (JUPYTER_SERVER mode)
    2. Attachments     — notebook_path → kernel_id  (explicit association)

    Notebooks and kernels are created and deleted independently.
    attach() / detach() link them when needed for execution.
    """

    def __init__(self):
        # kernel_id → KernelClient | dict
        self._kernel_clients: Dict[str, Any] = {}
        # notebook_path → kernel_id
        self._attachments: Dict[str, str] = {}
        # For legacy /api/connect route
        self._default_kernel_id: Optional[str] = None

    # -------------------------------------------------------------------------
    # Kernel client registry
    # -------------------------------------------------------------------------

    def add_kernel_client(self, kernel_id: str, client: Any) -> None:
        """Register a kernel client (KernelClient or metadata dict)."""
        self._kernel_clients[kernel_id] = client

    def get_kernel_client(self, kernel_id: str) -> Optional[Any]:
        """Return the kernel client for a given kernel_id, or None."""
        return self._kernel_clients.get(kernel_id)

    def remove_kernel_client(self, kernel_id: str) -> bool:
        """Remove a kernel client. Returns True if it existed."""
        if kernel_id in self._kernel_clients:
            del self._kernel_clients[kernel_id]
            return True
        return False

    def list_kernel_clients(self) -> Dict[str, Any]:
        """Return a copy of the kernel_id → client mapping."""
        return dict(self._kernel_clients)

    # -------------------------------------------------------------------------
    # Attachment registry  (notebook_path ↔ kernel_id)
    # -------------------------------------------------------------------------

    def attach(self, notebook_path: str, kernel_id: str) -> None:
        """Associate notebook_path with kernel_id."""
        self._attachments[notebook_path] = kernel_id

    def detach(self, notebook_path: str) -> bool:
        """Remove the association for notebook_path. Returns True if existed."""
        if notebook_path in self._attachments:
            del self._attachments[notebook_path]
            return True
        return False

    def detach_by_kernel(self, kernel_id: str) -> List[str]:
        """Remove all attachments pointing to kernel_id. Returns detached paths."""
        paths = [p for p, k in self._attachments.items() if k == kernel_id]
        for p in paths:
            del self._attachments[p]
        return paths

    def get_kernel_id(self, notebook_path: str) -> Optional[str]:
        """Return the kernel_id attached to this notebook_path, or None."""
        return self._attachments.get(notebook_path)

    def list_attachments(self) -> Dict[str, str]:
        """Return a copy of the path → kernel_id mapping."""
        return dict(self._attachments)

    # -------------------------------------------------------------------------
    # Notebook WebSocket connection (MCP_SERVER mode)
    # -------------------------------------------------------------------------

    def get_notebook_connection(self, notebook_path: str) -> NotebookConnection:
        """Return a context manager for a WebSocket notebook connection."""
        return NotebookConnection(notebook_path)

    # -------------------------------------------------------------------------
    # Kernel restart (MCP_SERVER mode — operates on KernelClient)
    # -------------------------------------------------------------------------

    def restart_kernel_client(self, kernel_id: str) -> bool:
        """Restart the kernel client. Returns True on success."""
        client = self._kernel_clients.get(kernel_id)
        if client and hasattr(client, "restart"):
            try:
                client.restart()
                return True
            except Exception:
                return False
        return False

    # -------------------------------------------------------------------------
    # Legacy helpers for /api/connect, /api/stop, /api/healthz routes
    # -------------------------------------------------------------------------

    def set_default_kernel(self, kernel_id: str, client: Any) -> None:
        """Store a 'default' kernel for the legacy connect route."""
        self._default_kernel_id = kernel_id
        self.add_kernel_client(kernel_id, client)

    def get_default_kernel(self) -> Optional[Any]:
        """Return the default kernel client, or None."""
        if self._default_kernel_id:
            return self._kernel_clients.get(self._default_kernel_id)
        return None

    def clear_default_kernel(self) -> None:
        """Stop and remove the default kernel."""
        if self._default_kernel_id:
            client = self._kernel_clients.get(self._default_kernel_id)
            if client and hasattr(client, "stop"):
                try:
                    client.stop()
                except Exception:
                    pass
            self.remove_kernel_client(self._default_kernel_id)
            self.detach_by_kernel(self._default_kernel_id)
            self._default_kernel_id = None

    # -------------------------------------------------------------------------
    # Convenience
    # -------------------------------------------------------------------------

    def __contains__(self, notebook_path: str) -> bool:
        """True if notebook_path has an attached kernel."""
        return notebook_path in self._attachments
