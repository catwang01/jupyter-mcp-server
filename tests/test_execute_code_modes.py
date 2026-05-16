"""Unit tests for execute_code_tool — JUPYTER_SERVER (gateway) and MCP_SERVER (local) modes.

Regression coverage for the gateway kernel bug where execute_code_local directly
accessed client.iopub_channel.socket (ZMQ-only), failing with AssertionError on
gateway kernels (JRK) where the socket is WebSocket-based (ChannelQueue), not ZMQ.
"""

from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest

from jupyter_mcp_server.tools._base import ServerMode
from jupyter_mcp_server.tools.execute_code_tool import ExecuteCodeTool


# ---------------------------------------------------------------------------
# JUPYTER_SERVER mode — gateway kernel (JRK / remote kernel)
# ---------------------------------------------------------------------------

class TestExecuteCodeJupyterServerMode:

    @pytest.mark.asyncio
    async def test_uses_execution_stack_not_zmq(self):
        """JUPYTER_SERVER mode must route through execute_via_execution_stack.

        Regression: the old path called execute_code_local which directly accessed
        client.iopub_channel.socket expecting a ZMQ socket. Gateway kernels expose a
        ChannelQueue (WebSocket-backed) with no .socket attribute, so the assertion
        inside GatewayKernelClient.iopub_channel fired: AssertionError (no message).
        """
        tool = ExecuteCodeTool()

        mock_serverapp = MagicMock()
        mock_kernel_manager = MagicMock()
        mock_kernel_manager.parent = mock_serverapp

        with patch(
            "jupyter_mcp_server.utils.execute_via_execution_stack",
            new_callable=AsyncMock,
            return_value=["hello world\n"],
        ) as mock_exec:
            result = await tool.execute(
                mode=ServerMode.JUPYTER_SERVER,
                kernel_manager=mock_kernel_manager,
                code="print('hello world')",
                kernel_id="gateway-kernel-id",
                timeout=30,
                safe_extract_outputs_fn=MagicMock(),
            )

        mock_exec.assert_called_once_with(
            serverapp=mock_serverapp,
            kernel_id="gateway-kernel-id",
            code="print('hello world')",
            timeout=30,
            logger=ANY,
        )
        assert result == ["hello world\n"]

    @pytest.mark.asyncio
    async def test_gateway_kernel_returns_multiline_output(self):
        """Gateway kernel execution with multi-line output works end-to-end."""
        tool = ExecuteCodeTool()

        mock_kernel_manager = MagicMock()
        mock_kernel_manager.parent = MagicMock()

        expected = ["line1\nline2\n", "42"]

        with patch(
            "jupyter_mcp_server.utils.execute_via_execution_stack",
            new_callable=AsyncMock,
            return_value=expected,
        ):
            result = await tool.execute(
                mode=ServerMode.JUPYTER_SERVER,
                kernel_manager=mock_kernel_manager,
                code="print('line1'); print('line2'); 42",
                kernel_id="gateway-kernel-id",
                timeout=60,
                safe_extract_outputs_fn=MagicMock(),
            )

        assert result == expected

    @pytest.mark.asyncio
    async def test_gateway_kernel_execution_stack_error_propagates(self):
        """Errors from execute_via_execution_stack are returned as [ERROR: ...] strings."""
        tool = ExecuteCodeTool()

        mock_kernel_manager = MagicMock()
        mock_kernel_manager.parent = MagicMock()

        with patch(
            "jupyter_mcp_server.utils.execute_via_execution_stack",
            new_callable=AsyncMock,
            return_value=["[ERROR: RuntimeError: kernel not found]"],
        ):
            result = await tool.execute(
                mode=ServerMode.JUPYTER_SERVER,
                kernel_manager=mock_kernel_manager,
                code="1 + 1",
                kernel_id="bad-kernel-id",
                timeout=30,
                safe_extract_outputs_fn=MagicMock(),
            )

        assert result == ["[ERROR: RuntimeError: kernel not found]"]


# ---------------------------------------------------------------------------
# MCP_SERVER mode — local kernel (direct notebook_manager)
# ---------------------------------------------------------------------------

class TestExecuteCodeMcpServerMode:

    @pytest.mark.asyncio
    async def test_local_kernel_returns_output(self):
        """MCP_SERVER mode executes code via notebook_manager kernel client."""
        tool = ExecuteCodeTool()

        mock_outputs = {"outputs": [{"output_type": "stream", "name": "stdout", "text": "hello\n"}]}
        mock_kernel = MagicMock()
        mock_kernel.execute.return_value = mock_outputs

        mock_notebook_manager = MagicMock()
        mock_notebook_manager.get_kernel_client.return_value = mock_kernel

        async def noop_wait(k, max_wait_seconds=30):
            pass

        safe_extract = MagicMock(return_value=["hello\n"])

        result = await tool.execute(
            mode=ServerMode.MCP_SERVER,
            notebook_manager=mock_notebook_manager,
            code="print('hello')",
            kernel_id="local-kernel-id",
            timeout=30,
            wait_for_kernel_idle_fn=noop_wait,
            safe_extract_outputs_fn=safe_extract,
        )

        mock_notebook_manager.get_kernel_client.assert_called_once_with("local-kernel-id")
        mock_kernel.execute.assert_called_once_with("print('hello')")
        assert result == ["hello\n"]

    @pytest.mark.asyncio
    async def test_missing_kernel_returns_error_message(self):
        """MCP_SERVER mode returns [ERROR: ...] when kernel_id is not found."""
        tool = ExecuteCodeTool()

        mock_notebook_manager = MagicMock()
        mock_notebook_manager.get_kernel_client.return_value = None

        async def noop_wait(k, max_wait_seconds=30):
            pass

        result = await tool.execute(
            mode=ServerMode.MCP_SERVER,
            notebook_manager=mock_notebook_manager,
            code="print('hello')",
            kernel_id="nonexistent-kernel",
            timeout=30,
            wait_for_kernel_idle_fn=noop_wait,
            safe_extract_outputs_fn=MagicMock(),
        )

        assert len(result) == 1
        assert "[ERROR:" in result[0]
        assert "nonexistent-kernel" in result[0]

    @pytest.mark.asyncio
    async def test_kernel_execute_exception_returns_error_format(self):
        """MCP_SERVER mode wraps kernel exceptions in [ERROR: TypeName: message] format."""
        tool = ExecuteCodeTool()

        mock_kernel = MagicMock()
        mock_kernel.execute.side_effect = RuntimeError("connection lost")

        mock_notebook_manager = MagicMock()
        mock_notebook_manager.get_kernel_client.return_value = mock_kernel

        async def noop_wait(k, max_wait_seconds=30):
            pass

        result = await tool.execute(
            mode=ServerMode.MCP_SERVER,
            notebook_manager=mock_notebook_manager,
            code="x = 1",
            kernel_id="local-kernel-id",
            timeout=30,
            wait_for_kernel_idle_fn=noop_wait,
            safe_extract_outputs_fn=MagicMock(),
        )

        assert len(result) == 1
        assert result[0] == "[ERROR: RuntimeError: connection lost]"

    @pytest.mark.asyncio
    async def test_no_output_returns_placeholder(self):
        """MCP_SERVER mode returns placeholder when kernel produces no output."""
        tool = ExecuteCodeTool()

        mock_kernel = MagicMock()
        mock_kernel.execute.return_value = None

        mock_notebook_manager = MagicMock()
        mock_notebook_manager.get_kernel_client.return_value = mock_kernel

        async def noop_wait(k, max_wait_seconds=30):
            pass

        result = await tool.execute(
            mode=ServerMode.MCP_SERVER,
            notebook_manager=mock_notebook_manager,
            code="x = 1",
            kernel_id="local-kernel-id",
            timeout=30,
            wait_for_kernel_idle_fn=noop_wait,
            safe_extract_outputs_fn=MagicMock(return_value=[]),
        )

        assert result == ["[No output generated]"]
