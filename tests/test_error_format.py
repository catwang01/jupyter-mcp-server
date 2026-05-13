"""Unit tests for error message format — verifies [ERROR: TypeName: message] pattern."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jupyter_mcp_server.utils import (
    execute_code_local,
    execute_cell_local,
    execute_via_execution_stack,
)
from jupyter_mcp_server.tools.execute_code_tool import ExecuteCodeTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_serverapp():
    """Return a MagicMock that looks enough like a ServerApp for early-failure paths."""
    mock = MagicMock()
    mock.web_app.settings.get.return_value = None
    return mock


# ---------------------------------------------------------------------------
# execute_via_execution_stack
# ---------------------------------------------------------------------------

class TestExecuteViaExecutionStackErrorFormat:

    @pytest.mark.asyncio
    async def test_keyerror_includes_type_name(self):
        """KeyError str() is just the key — type name must be prepended."""
        mock_serverapp = _make_serverapp()
        mock_serverapp.extension_manager.extension_apps.get.side_effect = KeyError("nbmodel")

        result = await execute_via_execution_stack(mock_serverapp, "k1", "print()")

        assert len(result) == 1
        assert result[0] == "[ERROR: KeyError: 'nbmodel']"

    @pytest.mark.asyncio
    async def test_runtime_error_includes_type_name(self):
        mock_serverapp = _make_serverapp()
        mock_serverapp.extension_manager.extension_apps.get.return_value = set()

        result = await execute_via_execution_stack(mock_serverapp, "k1", "print()")

        assert len(result) == 1
        assert result[0].startswith("[ERROR: RuntimeError:")

    @pytest.mark.asyncio
    async def test_attribute_error_with_empty_message_includes_type(self):
        """AttributeError() with no args — str(e) is '' but type name should still appear."""
        mock_serverapp = _make_serverapp()
        mock_serverapp.extension_manager.extension_apps.get.side_effect = AttributeError()

        result = await execute_via_execution_stack(mock_serverapp, "k1", "print()")

        assert len(result) == 1
        assert result[0] == "[ERROR: AttributeError: ]"


# ---------------------------------------------------------------------------
# execute_code_local
# ---------------------------------------------------------------------------

class TestExecuteCodeLocalErrorFormat:

    @pytest.mark.asyncio
    async def test_keyerror_includes_type_name(self):
        mock_serverapp = _make_serverapp()
        mock_serverapp.kernel_manager.pinned_superclass.get_kernel.side_effect = KeyError("bad-id")

        result = await execute_code_local(mock_serverapp, "nb.ipynb", "x = 1", "k1")

        assert len(result) == 1
        assert result[0] == "[ERROR: KeyError: 'bad-id']"

    @pytest.mark.asyncio
    async def test_attribute_error_includes_type_name(self):
        mock_serverapp = _make_serverapp()
        # AttributeError with no message — str(e) == ""
        mock_serverapp.kernel_manager.pinned_superclass.get_kernel.side_effect = AttributeError()

        result = await execute_code_local(mock_serverapp, "nb.ipynb", "x = 1", "k1")

        assert len(result) == 1
        assert result[0] == "[ERROR: AttributeError: ]"

    @pytest.mark.asyncio
    async def test_value_error_includes_message(self):
        mock_serverapp = _make_serverapp()
        mock_serverapp.kernel_manager.pinned_superclass.get_kernel.side_effect = ValueError("invalid kernel state")

        result = await execute_code_local(mock_serverapp, "nb.ipynb", "x = 1", "k1")

        assert len(result) == 1
        assert result[0] == "[ERROR: ValueError: invalid kernel state]"


# ---------------------------------------------------------------------------
# execute_cell_local
# ---------------------------------------------------------------------------

class TestExecuteCellLocalErrorFormat:

    @pytest.mark.asyncio
    async def test_keyerror_includes_type_name(self):
        mock_serverapp = _make_serverapp()
        mock_serverapp.web_app.settings.get.side_effect = KeyError("file_id_manager")

        result = await execute_cell_local(mock_serverapp, "nb.ipynb", 0, "k1")

        assert len(result) == 1
        assert result[0] == "[ERROR: KeyError: 'file_id_manager']"

    @pytest.mark.asyncio
    async def test_attribute_error_with_empty_message(self):
        mock_serverapp = _make_serverapp()
        mock_serverapp.web_app.settings.get.side_effect = AttributeError()

        result = await execute_cell_local(mock_serverapp, "nb.ipynb", 0, "k1")

        assert len(result) == 1
        assert result[0] == "[ERROR: AttributeError: ]"


# ---------------------------------------------------------------------------
# ExecuteCodeTool._execute_ipython_code (MCP_SERVER mode)
# ---------------------------------------------------------------------------

class TestExecuteCodeToolErrorFormat:

    @pytest.mark.asyncio
    async def test_keyerror_includes_type_name(self):
        tool = ExecuteCodeTool()

        mock_kernel = MagicMock()
        mock_kernel.execute.side_effect = KeyError("output_key")

        mock_notebook_manager = MagicMock()
        mock_notebook_manager.get_kernel_client.return_value = mock_kernel

        async def noop_wait(k, max_wait_seconds=30):
            pass

        result = await tool._execute_via_notebook_manager(
            code="x = 1",
            timeout=10,
            notebook_manager=mock_notebook_manager,
            wait_for_kernel_idle_fn=noop_wait,
            safe_extract_outputs_fn=MagicMock(return_value=["ok"]),
            kernel_id="k1",
        )

        assert len(result) == 1
        assert result[0] == "[ERROR: KeyError: 'output_key']"

    @pytest.mark.asyncio
    async def test_attribute_error_with_empty_message(self):
        tool = ExecuteCodeTool()

        mock_kernel = MagicMock()
        mock_kernel.execute.side_effect = AttributeError()

        mock_notebook_manager = MagicMock()
        mock_notebook_manager.get_kernel_client.return_value = mock_kernel

        async def noop_wait(k, max_wait_seconds=30):
            pass

        result = await tool._execute_via_notebook_manager(
            code="x = 1",
            timeout=10,
            notebook_manager=mock_notebook_manager,
            wait_for_kernel_idle_fn=noop_wait,
            safe_extract_outputs_fn=MagicMock(return_value=["ok"]),
            kernel_id="k1",
        )

        assert len(result) == 1
        assert result[0] == "[ERROR: AttributeError: ]"
