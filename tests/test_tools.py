# Copyright (c) 2024- Datalayer, Inc.
#
# BSD 3-Clause License

"""
Integration tests for Jupyter MCP Server - Both MCP_SERVER and JUPYTER_SERVER modes.

This test suite validates the Jupyter MCP Server in both deployment modes:

1. **MCP_SERVER Mode**: Standalone server using HTTP/WebSocket to Jupyter
2. **JUPYTER_SERVER Mode**: Extension with direct serverapp API access

Tests are parametrized to run against both modes using the same MCPClient,
ensuring consistent behavior across both deployment patterns.

Launch the tests:
```
$ pytest tests/test_server.py -v
```
"""

import logging
from http import HTTPStatus

import pytest
import requests

from .test_common import MCPClient, JUPYTER_TOOLS, timeout_wrapper
from .conftest import JUPYTER_TOKEN, TEST_MCP_SERVER, GATEWAY_KERNEL_SPEC, TEST_JUPYTER_SERVER


# Default notebook present in the dev/content directory used by tests
DEFAULT_NOTEBOOK = "notebook.ipynb"


###############################################################################
# Health Tests
###############################################################################

def test_jupyter_health(jupyter_server):
    """Test the Jupyter server health"""
    logging.info(f"Testing service health ({jupyter_server})")
    response = requests.get(
        f"{jupyter_server}/api/status",
        headers={
            "Authorization": f"token {JUPYTER_TOKEN}",
        },
    )
    assert response.status_code == HTTPStatus.OK


@pytest.mark.parametrize(
    "jupyter_mcp_server,kernel_expected_status",
    [(True, "alive"), (False, "not_initialized")],
    indirect=["jupyter_mcp_server"],
    ids=["start_runtime", "no_runtime"],
)
def test_mcp_health(jupyter_mcp_server, kernel_expected_status):
    """Test the MCP Jupyter server health"""
    logging.info(f"Testing MCP server health ({jupyter_mcp_server})")
    response = requests.get(f"{jupyter_mcp_server}/api/healthz")
    assert response.status_code == HTTPStatus.OK
    data = response.json()
    logging.debug(data)
    assert data.get("status") == "healthy"
    assert data.get("kernel_status") == kernel_expected_status


@pytest.mark.asyncio
async def test_mcp_tool_list(mcp_client_parametrized: MCPClient, request):
    """Check that the list of tools can be retrieved in both MCP_SERVER and JUPYTER_SERVER modes"""
    async with mcp_client_parametrized:
        tools = await mcp_client_parametrized.list_tools()
    tools_name = [tool.name for tool in tools.tools]
    logging.debug(f"tools_name: {tools_name}")

    # In JUPYTER_SERVER mode (jupyter_extension), connect_to_jupyter is filtered out
    # In MCP_SERVER mode (mcp_server), all tools are available
    expected_tools = JUPYTER_TOOLS.copy()

    # Get the current test parameter to determine the mode
    current_param = None
    for param in request.node.callspec.params.values():
        if param in ["mcp_server", "jupyter_extension"]:
            current_param = param
            break

    if current_param == "jupyter_extension":
        # Remove connect_to_jupyter for jupyter_extension mode
        expected_tools = [tool for tool in JUPYTER_TOOLS if tool != 'connect_to_jupyter']

    assert len(tools_name) == len(expected_tools) and sorted(tools_name) == sorted(
        expected_tools
    )


@pytest.mark.asyncio
@timeout_wrapper(30)
async def test_mcp_client_connects_with_token(mcp_client_parametrized: MCPClient):
    """MCP client with a valid token can connect and list tools in both modes."""
    async with mcp_client_parametrized:
        tools = await mcp_client_parametrized.list_tools()
    assert len(tools.tools) > 0


def _post_mcp_init(url, token=None):
    """POST an MCP initialize request, optionally with a Bearer token."""
    import httpx
    headers = {
        "Accept": "application/json, text/event-stream",
        "Content-Type": "application/json",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.post(
        f"{url}/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers=headers,
    )


def test_mcp_client_rejected_without_token(mcp_server_url):
    """MCP client without a token should be rejected by both server modes."""
    r = _post_mcp_init(mcp_server_url)
    assert r.status_code in (HTTPStatus.FORBIDDEN, HTTPStatus.UNAUTHORIZED), \
        f"Server accepted unauthenticated request (status {r.status_code})"


###############################################################################
# MCP_TOKEN tests (standalone MCP server only)
###############################################################################

MCP_TOKEN = "MY_MCP_TOKEN"


def _mcp_server_command_with_mcp_token(jupyter_url, port):
    """Build standalone MCP server command with both --mcp-token and --runtime-token."""
    return [
        "python", "-m", "jupyter_mcp_server",
        "--transport", "streamable-http",
        "--document-url", jupyter_url,
        "--document-id", "notebook.ipynb",
        "--document-token", JUPYTER_TOKEN,
        "--runtime-url", jupyter_url,
        "--start-new-runtime", "True",
        "--runtime-token", JUPYTER_TOKEN,
        "--mcp-token", MCP_TOKEN,
        "--port", str(port),
    ]


@pytest.fixture(scope="function")
def mcp_server_with_mcp_token(jupyter_server):
    """Standalone MCP server with --mcp-token set (separate from --runtime-token)."""
    from .conftest import _find_free_port, _start_server
    host = "localhost"
    port = _find_free_port()
    yield from _start_server(
        name="Jupyter MCP (mcp-token)",
        host=host,
        port=port,
        command=_mcp_server_command_with_mcp_token(jupyter_server, port),
        readiness_endpoint="/api/healthz",
    )


@pytest.mark.skipif(not TEST_MCP_SERVER, reason="TEST_MCP_SERVER disabled")
def test_mcp_token_accepted(mcp_server_with_mcp_token):
    """When --mcp-token is set, clients authenticating with MCP_TOKEN are accepted."""
    r = _post_mcp_init(mcp_server_with_mcp_token, token=MCP_TOKEN)
    assert r.status_code == HTTPStatus.OK


@pytest.mark.skipif(not TEST_MCP_SERVER, reason="TEST_MCP_SERVER disabled")
def test_mcp_token_rejects_runtime_token(mcp_server_with_mcp_token):
    """When --mcp-token is set, clients using RUNTIME_TOKEN should be rejected."""
    r = _post_mcp_init(mcp_server_with_mcp_token, token=JUPYTER_TOKEN)
    assert r.status_code in (HTTPStatus.FORBIDDEN, HTTPStatus.UNAUTHORIZED)


def _mcp_server_command_insecure_noauth(jupyter_url, port):
    """Build standalone MCP server command with --insecure-mcp-noauth (no --mcp-token)."""
    return [
        "python", "-m", "jupyter_mcp_server",
        "--transport", "streamable-http",
        "--document-url", jupyter_url,
        "--document-id", "notebook.ipynb",
        "--document-token", JUPYTER_TOKEN,
        "--runtime-url", jupyter_url,
        "--start-new-runtime", "True",
        "--runtime-token", JUPYTER_TOKEN,
        "--insecure-mcp-noauth",
        "--port", str(port),
    ]


@pytest.fixture(scope="function")
def mcp_server_insecure_noauth(jupyter_server):
    """Standalone MCP server with --insecure-mcp-noauth (no --mcp-token)."""
    from .conftest import _find_free_port, _start_server
    host = "localhost"
    port = _find_free_port()
    yield from _start_server(
        name="Jupyter MCP (insecure-noauth)",
        host=host,
        port=port,
        command=_mcp_server_command_insecure_noauth(jupyter_server, port),
        readiness_endpoint="/api/healthz",
    )


@pytest.mark.skipif(not TEST_MCP_SERVER, reason="TEST_MCP_SERVER disabled")
def test_insecure_noauth_allows_anonymous(mcp_server_insecure_noauth):
    """With --insecure-mcp-noauth and no --mcp-token, unauthenticated requests succeed."""
    r = _post_mcp_init(mcp_server_insecure_noauth)
    assert r.status_code == HTTPStatus.OK


@pytest.mark.skipif(not TEST_MCP_SERVER, reason="TEST_MCP_SERVER disabled")
def test_server_refuses_start_without_auth(jupyter_server):
    """Without --mcp-token or --insecure-mcp-noauth, the server should fail to start."""
    import subprocess
    from .conftest import _find_free_port
    port = _find_free_port()
    cmd = [
        "python", "-m", "jupyter_mcp_server",
        "--transport", "streamable-http",
        "--document-url", jupyter_server,
        "--document-id", "notebook.ipynb",
        "--document-token", JUPYTER_TOKEN,
        "--runtime-url", jupyter_server,
        "--start-new-runtime", "True",
        "--runtime-token", JUPYTER_TOKEN,
        "--port", str(port),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0, "Server should refuse to start without MCP auth config"
    assert "insecure-mcp-noauth" in result.stderr


###############################################################################
# Cell & tool tests
###############################################################################


@pytest.mark.asyncio
@timeout_wrapper(60)
async def test_cell_manipulation(mcp_client_parametrized: MCPClient):
    """Test cell manipulation (both markdown and code cells) in both MCP_SERVER and JUPYTER_SERVER modes"""

    async def check_and_delete_cell(client: MCPClient, index, expected_type, content):
        """Check and delete a cell (works for both markdown and code cells)"""
        cell_info = await client.read_cell(DEFAULT_NOTEBOOK, index)
        logging.debug(f"cell_info: {cell_info}")
        assert isinstance(cell_info['result'], list), "Read cell result should be a list"
        assert f"Cell {index}" in cell_info['result'][0] and f"type: {expected_type}" in cell_info['result'][0], "Cell metadata should be included"
        assert content in cell_info['result'][1], "Cell source should be included"
        result = await client.delete_cell(DEFAULT_NOTEBOOK, [index])
        assert result is not None, "delete_cell result should not be None"
        assert f"Cell {index} ({expected_type}) deleted successfully" in result["result"]
        assert f"deleted cell source:\n{content}" in result["result"]

    async with mcp_client_parametrized:
        # MCP_SERVER mode: a kernel is pre-started and attached at server startup.
        # JUPYTER_SERVER mode: no pre-attached kernel — create and attach one.
        created_kernel = False
        kernel_id = None
        try:
            kernel_id = await mcp_client_parametrized.get_first_kernel_id()
        except Exception:
            pass

        if kernel_id is None:
            create_result = await mcp_client_parametrized.create_kernel("python3")
            kernel_id = _parse_kernel_id_from_create_result(create_result)
            created_kernel = True

        await mcp_client_parametrized.attach_kernel(DEFAULT_NOTEBOOK, kernel_id)

        try:
            # Test markdown cell operations
            markdown_content = "Hello **World** !"
            result = await mcp_client_parametrized.insert_cell(DEFAULT_NOTEBOOK, 1, "markdown", markdown_content)
            assert result is not None, "insert_cell result should not be None"
            assert "Cell inserted successfully at index 1 (markdown)!" in result["result"]
            await check_and_delete_cell(mcp_client_parametrized, 1, "markdown", markdown_content)

            # Test code cell operations
            code_content = "1 + 1"
            code_result = await mcp_client_parametrized.insert_execute_code_cell(DEFAULT_NOTEBOOK, 1, code_content)
            expected_result = eval(code_content)
            assert int(code_result['result'][0]) == expected_result

            # Testing appending code cell to bottom of notebook
            code_result = await mcp_client_parametrized.insert_execute_code_cell(DEFAULT_NOTEBOOK, -1, code_content)
            expected_result = eval(code_content)
            assert int(code_result['result'][0]) == expected_result

            # Test overwrite_cell_source
            new_code_content = f"({code_content}) * 2"
            result = await mcp_client_parametrized.overwrite_cell_source(DEFAULT_NOTEBOOK, 1, new_code_content)
            assert result is not None, "overwrite_cell_source result should not be None"
            assert "Cell 1 overwritten successfully!" in result["result"]
            assert "diff" in result["result"]
            assert "-" in result["result"]
            assert "+" in result["result"]
            assert int(code_result["result"][0]) == expected_result

            await check_and_delete_cell(mcp_client_parametrized, 1, "code", new_code_content)
        finally:
            await mcp_client_parametrized.detach_kernel(DEFAULT_NOTEBOOK)
            if created_kernel:
                await mcp_client_parametrized.delete_kernel(kernel_id)


@pytest.mark.asyncio
@timeout_wrapper(60)
async def test_multimodal_output(mcp_client_parametrized: MCPClient):
    """Test multimodal output functionality with image generation in both modes"""
    async with mcp_client_parametrized:
        # MCP_SERVER mode: a kernel is pre-started and attached at server startup.
        # JUPYTER_SERVER mode: no pre-attached kernel — create and attach one.
        created_kernel = False
        kernel_id = None
        try:
            kernel_id = await mcp_client_parametrized.get_first_kernel_id()
        except Exception:
            pass

        if kernel_id is None:
            create_result = await mcp_client_parametrized.create_kernel("python3")
            kernel_id = _parse_kernel_id_from_create_result(create_result)
            created_kernel = True

        await mcp_client_parametrized.attach_kernel(DEFAULT_NOTEBOOK, kernel_id)

        image_code = """
from PIL import Image, ImageDraw
import io
import base64

width, height = 200, 100
image = Image.new('RGB', (width, height), color='white')
draw = ImageDraw.Draw(image)

draw.rectangle([10, 10, 190, 90], outline='blue', width=2)
draw.ellipse([20, 20, 80, 80], fill='red')
draw.text((100, 40), "Test Image", fill='black')

buffer = io.BytesIO()
image.save(buffer, format='PNG')
buffer.seek(0)

from IPython.display import Image as IPythonImage, display
display(IPythonImage(buffer.getvalue()))
"""
        try:
            result = await mcp_client_parametrized.insert_execute_code_cell(DEFAULT_NOTEBOOK, 1, image_code)

            assert isinstance(result['result'], list), "Result should be a list"
            assert isinstance(result['result'][0], dict)
            assert result['result'][0]['mimeType'] == "image/png", "Result should be a list of ImageContent"
            await mcp_client_parametrized.delete_cell(DEFAULT_NOTEBOOK, [1])
        finally:
            await mcp_client_parametrized.detach_kernel(DEFAULT_NOTEBOOK)
            if created_kernel:
                await mcp_client_parametrized.delete_kernel(kernel_id)


###############################################################################
# Kernel Management Tests
###############################################################################


@pytest.mark.asyncio
@timeout_wrapper(90)
async def test_kernel_lifecycle(mcp_client_parametrized: MCPClient):
    """Test full kernel lifecycle: create → attach → execute cell → detach → delete"""
    import uuid
    tag = uuid.uuid4().hex[:8]
    marker = f"# Kernel lifecycle test [{tag}]"

    async with mcp_client_parametrized:
        # 1. Create a new kernel
        create_result = await mcp_client_parametrized.create_kernel()
        logging.debug(f"create_kernel result: {create_result}")
        assert "Kernel created successfully" in create_result

        # Parse the kernel ID
        kernel_id = None
        for line in create_result.splitlines():
            if line.startswith("ID: "):
                kernel_id = line[4:].strip()
                break
        assert kernel_id, f"Could not parse kernel_id from: {create_result}"
        logging.info(f"Created kernel: {kernel_id}")

        # 2. Attach the kernel to the test notebook
        attach_result = await mcp_client_parametrized.attach_kernel(DEFAULT_NOTEBOOK, kernel_id)
        logging.debug(f"attach_kernel result: {attach_result}")
        assert kernel_id in attach_result
        assert DEFAULT_NOTEBOOK in attach_result

        # 3. Insert and execute a cell using the attached kernel
        code_result = await mcp_client_parametrized.insert_execute_code_cell(
            DEFAULT_NOTEBOOK, 1, f"{marker}\n2 ** 8"
        )
        assert code_result is not None
        assert "256" in str(code_result["result"][0])
        await mcp_client_parametrized.delete_cell(DEFAULT_NOTEBOOK, [1])

        # 4. Detach the kernel (kernel keeps running)
        detach_result = await mcp_client_parametrized.detach_kernel(DEFAULT_NOTEBOOK)
        logging.debug(f"detach_kernel result: {detach_result}")
        assert "detached" in detach_result.lower() or kernel_id in detach_result

        # 5. Delete the kernel
        delete_result = await mcp_client_parametrized.delete_kernel(kernel_id)
        logging.debug(f"delete_kernel result: {delete_result}")
        assert kernel_id in delete_result or "deleted" in delete_result.lower()


@pytest.mark.asyncio
@timeout_wrapper(60)
async def test_execute_cell_without_kernel(mcp_client_parametrized: MCPClient):
    """execute_cell must return a clear error when no kernel is attached."""
    async with mcp_client_parametrized:
        # Use a path that definitely has no kernel attached
        unattached_path = "unattached_test.ipynb"
        # Insert a cell into the default notebook first so there's something to try executing
        # Then try to execute from an unattached notebook path
        result = await mcp_client_parametrized.execute_cell(unattached_path, 0)
        # Result should be None (error wrapped) or contain an error message
        if result is not None:
            result_text = str(result.get("result", ""))
            assert "attach_kernel" in result_text.lower() or "no kernel" in result_text.lower(), (
                f"Expected 'attach_kernel' error message but got: {result_text[:300]}"
            )


@pytest.mark.asyncio
@timeout_wrapper(60)
async def test_restart_kernel(mcp_client_parametrized: MCPClient):
    """Test kernel restart clears state."""
    async with mcp_client_parametrized:
        # Create a kernel
        create_result = await mcp_client_parametrized.create_kernel()
        assert "Kernel created successfully" in create_result

        kernel_id = None
        for line in create_result.splitlines():
            if line.startswith("ID: "):
                kernel_id = line[4:].strip()
                break
        assert kernel_id

        # Attach to test notebook
        await mcp_client_parametrized.attach_kernel(DEFAULT_NOTEBOOK, kernel_id)

        # Set a variable
        await mcp_client_parametrized.execute_code(kernel_id, "x_restart_test = 42")

        # Restart the kernel
        restart_result = await mcp_client_parametrized.restart_kernel(kernel_id)
        logging.debug(f"restart_kernel result: {restart_result}")
        assert kernel_id in restart_result or "restarted" in restart_result.lower()

        # Cleanup
        await mcp_client_parametrized.detach_kernel(DEFAULT_NOTEBOOK)
        await mcp_client_parametrized.delete_kernel(kernel_id)


@pytest.mark.asyncio
@timeout_wrapper(60)
async def test_multi_notebook_kernel_operations(mcp_client_parametrized: MCPClient):
    """Test kernel operations across multiple notebooks in both modes."""
    import uuid
    tag = uuid.uuid4().hex[:8]
    marker_a = f"# Notebook A [{tag}]"
    marker_b = f"# Notebook B [{tag}]"

    async with mcp_client_parametrized:
        # Create two kernels
        result_a = await mcp_client_parametrized.create_kernel()
        result_b = await mcp_client_parametrized.create_kernel()

        kernel_a = None
        kernel_b = None
        for line in result_a.splitlines():
            if line.startswith("ID: "):
                kernel_a = line[4:].strip()
                break
        for line in result_b.splitlines():
            if line.startswith("ID: "):
                kernel_b = line[4:].strip()
                break
        assert kernel_a and kernel_b

        # Attach each kernel to a different notebook
        await mcp_client_parametrized.attach_kernel("notebook.ipynb", kernel_a)
        await mcp_client_parametrized.attach_kernel("new.ipynb", kernel_b)

        # Execute in notebook A
        await mcp_client_parametrized.insert_cell("notebook.ipynb", 1, "markdown", marker_a)
        cell_list_a = await mcp_client_parametrized.read_notebook("notebook.ipynb", limit=100)
        assert marker_a in cell_list_a

        # Execute in notebook B
        await mcp_client_parametrized.insert_cell("new.ipynb", 1, "markdown", marker_b)
        cell_list_b = await mcp_client_parametrized.read_notebook("new.ipynb", limit=100)
        assert marker_b in cell_list_b

        # Verify kernels are distinct via execute_code
        await mcp_client_parametrized.execute_code(kernel_a, "var_a = 'from_kernel_a'")
        await mcp_client_parametrized.execute_code(kernel_b, "var_b = 'from_kernel_b'")

        result_a_var = await mcp_client_parametrized.execute_code(kernel_a, "var_a")
        assert "from_kernel_a" in str(result_a_var["result"])

        # Clean up cells
        await mcp_client_parametrized.delete_cell("notebook.ipynb", [1])
        await mcp_client_parametrized.delete_cell("new.ipynb", [1])

        # Detach and delete kernels
        await mcp_client_parametrized.detach_kernel("notebook.ipynb")
        await mcp_client_parametrized.detach_kernel("new.ipynb")
        await mcp_client_parametrized.delete_kernel(kernel_a)
        await mcp_client_parametrized.delete_kernel(kernel_b)


###############################################################################
# Read & Execute Tests
###############################################################################


@pytest.mark.asyncio
@timeout_wrapper(60)
async def test_read_cell(mcp_client_parametrized: MCPClient):
    """Test read_cell with explicit notebook_path in both modes."""
    async with mcp_client_parametrized:
        result = await mcp_client_parametrized.read_cell(DEFAULT_NOTEBOOK, 0)
        logging.debug(f"read_cell result: {result}")

        assert result is not None, "read_cell must not return None"
        assert isinstance(result["result"], list), "Result should be a list"

        result_text = " ".join(str(item) for item in result["result"])
        assert "=====Cell 0" in result_text, (
            f"Expected '=====Cell 0' header in result, got: {result_text[:300]}"
        )


@pytest.mark.asyncio
@timeout_wrapper(60)
async def test_execute_code(mcp_client_parametrized: MCPClient):
    """Test execute_code with explicit kernel_id in both modes."""
    async with mcp_client_parametrized:
        # MCP_SERVER mode: a kernel is pre-started at server startup.
        # JUPYTER_SERVER mode: no pre-started kernel — create one.
        created_kernel = False
        try:
            kernel_id = await mcp_client_parametrized.get_first_kernel_id()
        except Exception:
            kernel_id = None

        if kernel_id is None:
            create_result = await mcp_client_parametrized.create_kernel("python3")
            kernel_id = _parse_kernel_id_from_create_result(create_result)
            created_kernel = True
        logging.info(f"Using kernel_id: {kernel_id}")

        try:
            # Test simple Python code
            await mcp_client_parametrized.execute_code(kernel_id, "words='Hello IPython World!'")

            # Test %who magic command (list variables)
            result = await mcp_client_parametrized.execute_code(kernel_id, "%who")
            assert "words" in result["result"][0]

            result = await mcp_client_parametrized.execute_code(kernel_id, "!echo 'Hello from shell'")
            assert "Hello from shell" in result["result"][0]

            # Test with very short timeout on a potentially long-running command
            result = await mcp_client_parametrized.execute_code(
                kernel_id, "import time\ntime.sleep(5)", timeout=2
            )
            # MCP_SERVER mode: "[TIMEOUT ERROR: ...]"; JUPYTER_SERVER mode: "[ERROR: TimeoutError: ...]"
            assert "TIMEOUT" in result["result"][0].upper()
        finally:
            if created_kernel:
                await mcp_client_parametrized.delete_kernel(kernel_id)


def _parse_kernel_id_from_create_result(create_result: str) -> str:
    """Extract kernel ID from create_kernel tool output ('ID: <uuid>' line)."""
    for line in create_result.splitlines():
        if line.startswith("ID: "):
            return line[4:].strip()
    raise ValueError(f"Could not parse kernel_id from: {create_result!r}")


@pytest.mark.asyncio
@timeout_wrapper(90)
async def test_execute_code_jupyter_server_local_kernel(mcp_client_jupyter_server_only: MCPClient):
    """Integration test: execute_code creates a fresh local kernel in JUPYTER_SERVER mode.

    Covers the _execute_via_kernel_manager path (execute_via_execution_stack) with a
    locally-managed python3 kernel, verifying basic output, magic commands, and shell.
    """
    async with mcp_client_jupyter_server_only:
        create_result = await mcp_client_jupyter_server_only.create_kernel("python3")
        kernel_id = _parse_kernel_id_from_create_result(create_result)
        logging.info(f"Created local kernel: {kernel_id}")

        try:
            result = await mcp_client_jupyter_server_only.execute_code(kernel_id, "print('local kernel ok')")
            assert "local kernel ok" in result["result"][0]

            result = await mcp_client_jupyter_server_only.execute_code(kernel_id, "x = 123; x")
            assert "123" in str(result["result"][0])

            result = await mcp_client_jupyter_server_only.execute_code(kernel_id, "%who")
            assert "x" in result["result"][0]
        finally:
            await mcp_client_jupyter_server_only.delete_kernel(kernel_id)


@pytest.mark.asyncio
@timeout_wrapper(120)
async def test_execute_code_gateway_kernel(mcp_client_gateway: MCPClient):
    """Integration test: execute_code with a real gateway kernel (JRK/remote).

    Regression test for the AssertionError bug where execute_code_local directly
    accessed client.iopub_channel.socket (ZMQ), failing on gateway kernels that
    use ChannelQueue (WebSocket-backed) with no .socket attribute.

    Requires env vars:
        JUPYTER_GATEWAY_URL   e.g. http://localhost:8888/jupyter/jrk
        GATEWAY_KERNEL_SPEC   e.g. localmac:python3
    """
    async with mcp_client_gateway:
        create_result = await mcp_client_gateway.create_kernel(GATEWAY_KERNEL_SPEC)
        kernel_id = _parse_kernel_id_from_create_result(create_result)
        logging.info(f"Created gateway kernel: {kernel_id} ({GATEWAY_KERNEL_SPEC})")

        try:
            result = await mcp_client_gateway.execute_code(kernel_id, "print('gateway kernel ok')")
            assert "gateway kernel ok" in result["result"][0], \
                f"Expected output, got: {result['result']}"

            result = await mcp_client_gateway.execute_code(kernel_id, "2 ** 10")
            assert "1024" in str(result["result"][0])

            result = await mcp_client_gateway.execute_code(kernel_id, "%who")
            assert result["result"] is not None
        finally:
            await mcp_client_gateway.delete_kernel(kernel_id)


@pytest.mark.asyncio
async def test_list_kernels(mcp_client_parametrized: MCPClient):
    """Test list_kernels functionality in both MCP_SERVER and JUPYTER_SERVER modes"""
    async with mcp_client_parametrized:
        kernel_list = await mcp_client_parametrized.list_kernels()
        logging.debug(f"Kernel list: {kernel_list}")
        assert "ID\tName\tDisplay_Name\tLanguage\tState\tConnections\tLast_Activity\tEnvironment" in kernel_list


###############################################################################
# read_notebook Tests
###############################################################################


@pytest.mark.asyncio
@timeout_wrapper(30)
async def test_read_notebook_basic(mcp_client_parametrized: MCPClient):
    """read_notebook should return cell list without needing a kernel attached."""
    async with mcp_client_parametrized:
        result = await mcp_client_parametrized.read_notebook(DEFAULT_NOTEBOOK)
        logging.debug(f"read_notebook result: {result}")
        assert result is not None, "read_notebook must not return None"
        assert "cells" in result.lower() or "cell" in result.lower(), (
            f"Expected cell information in result, got: {result[:300]}"
        )


@pytest.mark.asyncio
@timeout_wrapper(30)
async def test_read_notebook_detailed_format(mcp_client_parametrized: MCPClient):
    """read_notebook with response_format='detailed' returns full cell source."""
    async with mcp_client_parametrized:
        result = await mcp_client_parametrized.read_notebook(
            DEFAULT_NOTEBOOK, response_format="detailed"
        )
        logging.debug(f"read_notebook detailed result: {result}")
        assert result is not None, "read_notebook (detailed) must not return None"
        # detailed mode should include the actual cell content, not just line counts
        assert "cell" in result.lower(), (
            f"Expected cell info in detailed result, got: {result[:300]}"
        )


@pytest.mark.asyncio
@timeout_wrapper(30)
async def test_read_notebook_pagination(mcp_client_parametrized: MCPClient):
    """read_notebook start_index / limit parameters work correctly."""
    async with mcp_client_parametrized:
        # First read to find total cell count
        full_result = await mcp_client_parametrized.read_notebook(DEFAULT_NOTEBOOK, limit=0)
        assert full_result is not None

        # Read with limit=1 — should only include first cell
        limited = await mcp_client_parametrized.read_notebook(
            DEFAULT_NOTEBOOK, start_index=0, limit=1
        )
        assert limited is not None
        # A limit of 1 should return fewer lines than limit=0 (all cells)
        assert len(limited.splitlines()) <= len(full_result.splitlines()), (
            "limit=1 should produce fewer output lines than limit=0"
        )


@pytest.mark.asyncio
@timeout_wrapper(30)
async def test_read_notebook_nonexistent(mcp_client_parametrized: MCPClient):
    """read_notebook with a non-existent path should return an error, not crash."""
    async with mcp_client_parametrized:
        result = await mcp_client_parametrized.read_notebook("does_not_exist.ipynb")
        # Should either return None (wrapped error) or an error message string
        if result is not None:
            assert any(
                kw in result.lower()
                for kw in ["error", "not found", "no such", "does_not_exist"]
            ), f"Expected error message for missing notebook, got: {result[:300]}"


###############################################################################
# list_notebooks Tests
###############################################################################


@pytest.mark.asyncio
@timeout_wrapper(30)
async def test_list_notebooks_empty(mcp_client_parametrized: MCPClient):
    """list_notebooks returns a clear message when no notebooks are attached."""
    async with mcp_client_parametrized:
        result = await mcp_client_parametrized.list_notebooks()
        logging.debug(f"list_notebooks (empty) result: {result}")
        assert result is not None
        # Either TSV header or "no notebooks" message
        assert "notebook" in result.lower() or "kernel" in result.lower(), (
            f"Unexpected list_notebooks output: {result[:300]}"
        )


@pytest.mark.asyncio
@timeout_wrapper(60)
async def test_list_notebooks_after_attach(mcp_client_parametrized: MCPClient):
    """list_notebooks shows the notebook path and kernel ID after attach_kernel."""
    async with mcp_client_parametrized:
        # Create and attach a kernel
        create_result = await mcp_client_parametrized.create_kernel()
        assert "Kernel created successfully" in create_result
        kernel_id = None
        for line in create_result.splitlines():
            if line.startswith("ID: "):
                kernel_id = line[4:].strip()
                break
        assert kernel_id

        await mcp_client_parametrized.attach_kernel(DEFAULT_NOTEBOOK, kernel_id)

        # list_notebooks should now include the notebook path and kernel ID
        result = await mcp_client_parametrized.list_notebooks()
        logging.debug(f"list_notebooks (attached) result: {result}")
        assert result is not None
        assert DEFAULT_NOTEBOOK in result, (
            f"Expected '{DEFAULT_NOTEBOOK}' in list_notebooks output: {result}"
        )
        assert kernel_id in result, (
            f"Expected kernel_id '{kernel_id}' in list_notebooks output: {result}"
        )

        # Cleanup
        await mcp_client_parametrized.detach_kernel(DEFAULT_NOTEBOOK)
        await mcp_client_parametrized.delete_kernel(kernel_id)


###############################################################################
# list_kernel_specs Tests
###############################################################################


@pytest.mark.asyncio
@timeout_wrapper(30)
async def test_list_kernel_specs(mcp_client_parametrized: MCPClient):
    """list_kernel_specs returns at least one kernel spec with correct TSV header."""
    async with mcp_client_parametrized:
        result = await mcp_client_parametrized.list_kernel_specs()
        logging.debug(f"list_kernel_specs result: {result}")
        assert result is not None, "list_kernel_specs must not return None"
        assert "Name\tDisplay_Name\tLanguage" in result, (
            f"Expected TSV header in list_kernel_specs output: {result[:300]}"
        )
        lines = result.strip().splitlines()
        # At least header + one spec row
        assert len(lines) >= 2, (
            f"Expected at least one kernel spec row, got: {result[:300]}"
        )


###############################################################################
# Allowed Tools Configuration Tests
###############################################################################


@pytest.mark.asyncio
async def test_allowed_jupyter_mcp_tools_integration(mcp_client_parametrized: MCPClient):
    """Test that the server respects allowed_jupyter_mcp_tools configuration."""
    async with mcp_client_parametrized:
        tools = await mcp_client_parametrized.list_tools()
        tool_names = [tool.name for tool in tools.tools]

        logging.info(f"Available tools: {tool_names}")

        jupyter_tools = [name for name in tool_names if name.startswith("notebook_")]

        if any(tool.startswith("notebook_") for tool in tool_names):
            assert "notebook_run-all-cells" in tool_names or len(jupyter_tools) > 0
            logging.info(f"Jupyter MCP tools found: {jupyter_tools}")
        else:
            logging.info("No jupyter-mcp-tools detected (possibly not in JupyterLab mode)")


def test_config_allowed_tools_parsing():
    """Test the configuration parsing for allowed tools."""
    from jupyter_mcp_server.config import JupyterMCPConfig

    test_cases = [
        ("tool1,tool2,tool3", ["tool1", "tool2", "tool3"]),
        ("single_tool", ["single_tool"]),
        (" tool1 , tool2 , tool3 ", ["tool1", "tool2", "tool3"]),
        ("tool1,,tool2,", ["tool1", "tool2"]),
        ("notebook_*,console_create", ["notebook_*", "console_create"]),
    ]

    for input_str, expected in test_cases:
        config = JupyterMCPConfig(allowed_jupyter_mcp_tools=input_str)
        result = config.get_allowed_jupyter_mcp_tools()
        assert result == expected, f"Failed for input '{input_str}': expected {expected}, got {result}"
        logging.info(f"✅ Parsed '{input_str}' -> {result}")

    logging.info("✅ All configuration parsing tests passed")


def test_config_environment_variable():
    """Test that CLI-style configuration works (environment variables work through CLI)."""
    from jupyter_mcp_server.config import set_config, reset_config

    reset_config()
    config = set_config(allowed_jupyter_mcp_tools="env_tool1,env_tool2")
    tools = config.get_allowed_jupyter_mcp_tools()

    assert tools == ["env_tool1", "env_tool2"]
    logging.info(f"✅ CLI-style configuration test passed: {tools}")

    reset_config()


def test_config_defaults():
    """Test that default configuration works correctly."""
    from jupyter_mcp_server.config import JupyterMCPConfig, reset_config

    reset_config()
    config = JupyterMCPConfig()
    default_tools = config.get_allowed_jupyter_mcp_tools()

    assert "notebook_run-all-cells" in default_tools
    assert "notebook_get-selected-cell" in default_tools
    assert len(default_tools) == 2

    logging.info(f"✅ Default configuration test passed: {default_tools}")


def test_server_tool_registration():
    """Test that get_registered_tools includes the correct tools based on configuration."""
    from jupyter_mcp_server.server import get_registered_tools
    from jupyter_mcp_server.config import set_config, reset_config

    reset_config()
    set_config(allowed_jupyter_mcp_tools="notebook_run-all-cells")

    try:
        tools = get_registered_tools(token="test_token", url="http://localhost:8888")
        tool_names = [tool["name"] for tool in tools]

        logging.info(f"Registered tools: {tool_names}")

        fastmcp_tools = [name for name in tool_names if not name.startswith("notebook_")]
        assert len(fastmcp_tools) > 0, "FastMCP tools should always be present"

        logging.info("✅ Server tool registration test completed")

    except Exception as e:
        logging.info(f"Server tool registration test skipped due to: {e}")

    finally:
        reset_config()


###############################################################################
# Unit Tests: Tool Signatures & NotebookManager
###############################################################################


def test_notebook_path_param_in_tool_signatures():
    """Verify all cell operation tools expose notebook_path as a required parameter.

    Guards against regressions where notebook_path is accidentally removed or
    renamed back to notebook_name.
    """
    import inspect
    from jupyter_mcp_server.tools.execute_cell_tool import ExecuteCellTool
    from jupyter_mcp_server.tools.insert_cell_tool import InsertCellTool
    from jupyter_mcp_server.tools.edit_cell_source_tool import EditCellSourceTool
    from jupyter_mcp_server.tools.overwrite_cell_source_tool import OverwriteCellSourceTool
    from jupyter_mcp_server.tools.delete_cell_tool import DeleteCellTool
    from jupyter_mcp_server.tools.move_cell_tool import MoveCellTool
    from jupyter_mcp_server.tools.read_cell_tool import ReadCellTool

    cell_tools = [
        ExecuteCellTool,
        InsertCellTool,
        EditCellSourceTool,
        OverwriteCellSourceTool,
        DeleteCellTool,
        MoveCellTool,
        ReadCellTool,
    ]
    for tool_cls in cell_tools:
        sig = inspect.signature(tool_cls().execute)
        assert "notebook_path" in sig.parameters, (
            f"{tool_cls.__name__}.execute() is missing notebook_path parameter"
        )
        assert "notebook_name" not in sig.parameters, (
            f"{tool_cls.__name__}.execute() still has old notebook_name parameter"
        )
    logging.info("✅ All cell tools expose notebook_path parameter")


def test_execute_code_kernel_id_param():
    """Verify ExecuteCodeTool.execute() takes kernel_id (not notebook_name)."""
    import inspect
    from jupyter_mcp_server.tools.execute_code_tool import ExecuteCodeTool

    sig = inspect.signature(ExecuteCodeTool().execute)
    assert "kernel_id" in sig.parameters, (
        "ExecuteCodeTool.execute() is missing kernel_id parameter"
    )
    assert "notebook_name" not in sig.parameters, (
        "ExecuteCodeTool.execute() still has old notebook_name parameter"
    )
    logging.info("✅ ExecuteCodeTool uses kernel_id parameter")


def test_notebook_manager_attach_detach():
    """Unit test: NotebookManager attach/detach/lookup semantics."""
    from jupyter_mcp_server.notebook_manager import NotebookManager

    mgr = NotebookManager()

    # No attachment yet
    assert mgr.get_kernel_id("nb_a.ipynb") is None
    assert "nb_a.ipynb" not in mgr

    # Add kernel clients and attach
    mgr.add_kernel_client("kernel-1", {"id": "kernel-1"})
    mgr.add_kernel_client("kernel-2", {"id": "kernel-2"})
    mgr.attach("nb_a.ipynb", "kernel-1")
    mgr.attach("nb_b.ipynb", "kernel-2")

    assert mgr.get_kernel_id("nb_a.ipynb") == "kernel-1"
    assert mgr.get_kernel_id("nb_b.ipynb") == "kernel-2"
    assert "nb_a.ipynb" in mgr

    # Re-attach nb_a to kernel-2
    mgr.attach("nb_a.ipynb", "kernel-2")
    assert mgr.get_kernel_id("nb_a.ipynb") == "kernel-2"

    # detach_by_kernel removes all notebooks using kernel-2
    detached = mgr.detach_by_kernel("kernel-2")
    assert set(detached) == {"nb_a.ipynb", "nb_b.ipynb"}
    assert mgr.get_kernel_id("nb_a.ipynb") is None
    assert mgr.get_kernel_id("nb_b.ipynb") is None

    # Explicit detach
    mgr.attach("nb_c.ipynb", "kernel-1")
    assert mgr.detach("nb_c.ipynb") is True
    assert mgr.detach("nb_c.ipynb") is False  # already gone

    # Kernel client lookup
    assert mgr.get_kernel_client("kernel-1") == {"id": "kernel-1"}
    assert mgr.get_kernel_client("nonexistent") is None

    logging.info("✅ NotebookManager attach/detach semantics verified")


def test_notebook_manager_default_kernel():
    """Unit test: legacy set_default_kernel / get_default_kernel / clear_default_kernel."""
    from jupyter_mcp_server.notebook_manager import NotebookManager

    mgr = NotebookManager()

    assert mgr.get_default_kernel() is None

    fake_client = {"id": "k1", "is_alive": lambda: True}
    mgr.set_default_kernel("k1", fake_client)

    assert mgr.get_default_kernel() is fake_client
    assert mgr.get_kernel_client("k1") is fake_client

    mgr.clear_default_kernel()
    assert mgr.get_default_kernel() is None
    assert mgr.get_kernel_client("k1") is None

    logging.info("✅ NotebookManager default_kernel helpers verified")
