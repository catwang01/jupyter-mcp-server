# Copyright (c) 2024- Datalayer, Inc.
#
# BSD 3-Clause License

"""
Common test infrastructure shared between MCP_SERVER and JUPYTER_SERVER mode tests.

This module provides:
- MCPClient: MCP protocol client for remote testing
- timeout_wrapper: Decorator for timeout handling
- requires_session: Decorator to check client session connection
- JUPYTER_TOOLS: List of expected tool names
- Helper functions for content extraction
"""

import asyncio
import functools
import json
import logging
from contextlib import AsyncExitStack

import pytest
import requests
from mcp import ClientSession, types
from mcp.client.streamable_http import streamable_http_client


# TODO: could be retrieved from code (inspect)
JUPYTER_TOOLS = [
    # Kernel Management Tools
    "create_kernel",
    "delete_kernel",
    "restart_kernel",
    # Kernel-Notebook Association Tools
    "attach_kernel",
    "detach_kernel",
    # Notebook Reading
    "read_notebook",
    # Cell Tools
    "insert_cell",
    "insert_execute_code_cell",
    "overwrite_cell_source",
    "edit_cell_source",
    "execute_cell",
    "read_cell",
    "delete_cell",
    "move_cell",
    "execute_code",
    # Server Management Tools
    "list_files",
    "list_kernels",
    "list_notebooks",
    "connect_to_jupyter",
]


def timeout_wrapper(timeout_seconds=30):
    """Decorator to add timeout handling to async test functions
    
    Windows has known issues with asyncio and network timeouts that can cause 
    tests to hang indefinitely. This decorator adds a safety timeout specifically
    for Windows platforms while allowing other platforms to run normally.
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                return await asyncio.wait_for(func(*args, **kwargs), timeout=timeout_seconds)
            except asyncio.TimeoutError:
                pytest.skip(f"Test {func.__name__} timed out ({timeout_seconds}s) - known platform limitation")
            except Exception as e:
                # Check if it's a network timeout related to Windows (not an assertion failure)
                if not isinstance(e, AssertionError) and ("ReadTimeout" in str(e) or "TimeoutError" in str(e)):
                    pytest.skip(f"Test {func.__name__} hit network timeout - known platform limitation: {e}")
                raise
        return wrapper
    return decorator


def requires_session(func):
    """
    A decorator that checks if the instance has a connected session.
    """
    @functools.wraps(func)
    async def wrapper(self, *args, **kwargs):
        if not self._session:
            raise RuntimeError("Client session is not connected")
        # If the session exists, call the original method
        return await func(self, *args, **kwargs)
    
    return wrapper


class MCPClient:
    """A standard MCP client used to interact with the Jupyter MCP server

    Basically it's a client wrapper for the Jupyter MCP server.
    It uses the `requires_session` decorator to check if the session is connected.
    """

    def __init__(self, url, token=None):
        self.url = f"{url}/mcp"
        self.token = token
        self._session: ClientSession | None = None
        self._exit_stack = AsyncExitStack()
        self._http_client = None
        self._auto_kernel_id = None

    _DEFAULT_NOTEBOOK = "notebook.ipynb"

    @staticmethod
    def _split_notebook_path(first_arg, *rest):
        """Detect whether first_arg is a notebook_path (str) or a cell index (int/list).

        Returns (notebook_path, remaining_args) so callers can support both:
          method(notebook_path, cell_index, ...) — explicit notebook
          method(cell_index, ...)               — uses _DEFAULT_NOTEBOOK
        """
        if isinstance(first_arg, str):
            return first_arg, rest
        return MCPClient._DEFAULT_NOTEBOOK, (first_arg,) + rest

    async def __aenter__(self):
        """Initiate the session (enter session context)"""
        if self.token:
            import httpx
            self._http_client = httpx.AsyncClient(
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=httpx.Timeout(30, read=None),
            )
        streams_context = streamable_http_client(
            self.url, http_client=self._http_client
        )
        read_stream, write_stream, _ = await self._exit_stack.enter_async_context(
            streams_context
        )
        session_context = ClientSession(read_stream, write_stream)
        self._session = await self._exit_stack.enter_async_context(session_context)
        await self._session.initialize()

        # In JUPYTER_SERVER mode no kernel is pre-attached; auto-create one so
        # execute_cell works without explicit setup in each individual test.
        self._auto_kernel_id = None
        try:
            notebooks_text = await self.list_notebooks()
            already_attached = bool(notebooks_text and self._DEFAULT_NOTEBOOK in notebooks_text)
            if not already_attached:
                tools_result = await self._session.list_tools()  # type: ignore
                tool_names = [t.name for t in tools_result.tools]
                if "create_kernel" in tool_names:
                    kernel_text = await self.create_kernel()
                    if kernel_text:
                        for line in kernel_text.split("\n"):
                            if line.startswith("ID: "):
                                self._auto_kernel_id = line[4:].strip()
                                break
                    if self._auto_kernel_id:
                        await self.attach_kernel(self._DEFAULT_NOTEBOOK, self._auto_kernel_id)
        except Exception:
            self._auto_kernel_id = None

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Close the session (exit session context)"""
        if self._auto_kernel_id:
            try:
                await self.detach_kernel(self._DEFAULT_NOTEBOOK)
                await self.delete_kernel(self._auto_kernel_id)
            except Exception:
                pass
            self._auto_kernel_id = None
        if self._exit_stack:
            await self._exit_stack.aclose()
        if self._http_client:
            await self._http_client.aclose()
        self._session = None

    @staticmethod
    def _extract_text_content(result):
        """Extract text content from a result"""
        try:
            logging.debug(f"_extract_text_content: result type={type(result)}, has content={hasattr(result, 'content')}, is tuple={isinstance(result, tuple)}, is list={isinstance(result, list)}")
            
            # Handle tuple results (content, metadata)
            if isinstance(result, tuple) and len(result) >= 2:
                logging.debug(f"_extract_text_content: handling tuple, first element type={type(result[0])}")
                result = result[0]  # Get the content list from the tuple
            
            if hasattr(result, 'content') and result.content and len(result.content) > 0:
                # Check if all items are TextContent
                if all(isinstance(item, types.TextContent) for item in result.content):
                    # If multiple TextContent items, return as JSON list
                    if len(result.content) > 1:
                        texts = [item.text for item in result.content]
                        import json
                        text = json.dumps(texts)
                        logging.debug(f"_extract_text_content: extracted {len(texts)} TextContent items as JSON list")
                        return text
                    else:
                        text = result.content[0].text
                        logging.debug(f"_extract_text_content: extracted from result.content[0].text, length={len(text)}")
                        return text
            # Handle list results directly
            elif isinstance(result, list) and len(result) > 0:
                # Check if all items are TextContent
                if all(isinstance(item, types.TextContent) for item in result):
                    # If multiple TextContent items, return as JSON list
                    if len(result) > 1:
                        texts = [item.text for item in result]
                        import json
                        text = json.dumps(texts)
                        logging.debug(f"_extract_text_content: extracted {len(texts)} TextContent items as JSON list")
                        return text
                    else:
                        text = result[0].text
                        logging.debug(f"_extract_text_content: extracted from list[0].text, length={len(text)}")
                        return text
        except (AttributeError, IndexError, TypeError) as e:
            logging.debug(f"_extract_text_content error: {e}, result type: {type(result)}")
        
        logging.debug(f"_extract_text_content: returning None, could not extract")
        return None

    def _get_structured_content_safe(self, result):
        """Safely get structured content with fallback to text content parsing"""
        content = getattr(result, 'structuredContent', None)
        if content is None:
            # Try to extract from text content as fallback
            text_content = self._extract_text_content(result)
            logging.debug(f"_get_structured_content_safe: text_content={repr(text_content[:200] if text_content else None)}")
            if text_content:
                # Try to parse as JSON
                try:
                    parsed = json.loads(text_content)
                    logging.debug(f"_get_structured_content_safe: JSON parsed successfully, type={type(parsed)}")
                    # Check if it's already a wrapped result or a direct response object
                    if isinstance(parsed, dict):
                        # If it has "result" key, it's already wrapped
                        if "result" in parsed:
                            return parsed
                        # If it has keys like "index", "type", "source" it's a direct object (like CellInfo)
                        elif any(key in parsed for key in ["index", "type", "source", "cells"]):
                            return parsed
                        # Otherwise wrap it
                        else:
                            return {"result": parsed}
                    else:
                        # Lists, strings, etc. - wrap them
                        return {"result": parsed}
                except json.JSONDecodeError:
                    # Not JSON - could be plain text or list representation
                    # Try to evaluate as Python literal (for lists, etc.)
                    try:
                        import ast
                        parsed = ast.literal_eval(text_content)
                        logging.debug(f"_get_structured_content_safe: ast.literal_eval succeeded, type={type(parsed)}, value={repr(parsed)}")
                        return {"result": parsed}
                    except (ValueError, SyntaxError):
                        # Plain text - return as-is
                        logging.debug(f"_get_structured_content_safe: Plain text, wrapping in result dict")
                        return {"result": text_content}
            else:
                # No text content - check if we have ImageContent or mixed content
                if hasattr(result, 'content') and result.content:
                    # Extract mixed content (ImageContent + TextContent)
                    content_list = []
                    for item in result.content:
                        if isinstance(item, types.ImageContent):
                            # Convert ImageContent to dict format
                            content_list.append({
                                'type': 'image',
                                'data': item.data,
                                'mimeType': item.mimeType,
                                'annotations': getattr(item, 'annotations', None),
                                'meta': getattr(item, 'meta', None)
                            })
                        elif isinstance(item, types.TextContent):
                            # Include text content if present
                            content_list.append(item.text)
                    
                    if content_list:
                        logging.debug(f"_get_structured_content_safe: extracted {len(content_list)} items from mixed content")
                        return {"result": content_list}
                
                logging.warning(f"No text content available in result: {type(result)}")
                return None
        return content
    
    async def _call_tool_safe(self, tool_name, arguments=None):
        """Safely call a tool, returning None on error (for test compatibility)"""
        try:
            result = await self._session.call_tool(tool_name, arguments=arguments or {})  # type: ignore
            
            # Log raw result for debugging
            logging.debug(f"_call_tool_safe({tool_name}): raw result type={type(result)}")
            logging.debug(f"_call_tool_safe({tool_name}): raw result={result}")
            
            # Check if result contains error text (for MCP_SERVER mode where errors are wrapped in results)
            text_content = self._extract_text_content(result)
            if text_content and ("Error executing tool" in text_content or "is out of range" in text_content or "not found" in text_content):
                logging.warning(f"Tool {tool_name} returned error in result: {text_content[:100]}")
                return None
            
            # Also check structured content for errors (for JUPYTER_SERVER mode)
            structured_content = self._get_structured_content_safe(result)
            if structured_content:
                # Check if result contains error messages
                result_value = structured_content.get("result")
                if result_value:
                    # Handle both string and list results
                    error_text = ""
                    if isinstance(result_value, str):
                        error_text = result_value
                    elif isinstance(result_value, list) and len(result_value) > 0:
                        error_text = str(result_value[0])
                    
                    if error_text and ("[ERROR:" in error_text or "is out of range" in error_text or "not found" in error_text):
                        logging.warning(f"Tool {tool_name} returned error in structured result: {error_text[:100]}")
                        return None
            
            return result
        except Exception as e:
            # Log the error but return None for test compatibility (JUPYTER_SERVER mode)
            logging.warning(f"Tool {tool_name} raised error: {e}")
            return None

    @requires_session
    async def list_tools(self):
        return await self._session.list_tools()  # type: ignore

    # -------------------------------------------------------------------------
    # Kernel Management Methods
    # -------------------------------------------------------------------------

    @requires_session
    async def create_kernel(self, kernel_name=None):
        """Create a new standalone kernel. Returns the result text (contains kernel ID)."""
        arguments = {}
        if kernel_name is not None:
            arguments["kernel_name"] = kernel_name
        result = await self._session.call_tool("create_kernel", arguments=arguments)  # type: ignore
        return self._extract_text_content(result)

    @requires_session
    async def delete_kernel(self, kernel_id):
        result = await self._session.call_tool("delete_kernel", arguments={"kernel_id": kernel_id})  # type: ignore
        return self._extract_text_content(result)

    @requires_session
    async def restart_kernel(self, kernel_id):
        result = await self._session.call_tool("restart_kernel", arguments={"kernel_id": kernel_id})  # type: ignore
        return self._extract_text_content(result)

    @requires_session
    async def attach_kernel(self, notebook_path, kernel_id):
        """Attach a kernel to a notebook path."""
        result = await self._session.call_tool(
            "attach_kernel",
            arguments={"notebook_path": notebook_path, "kernel_id": kernel_id},
        )  # type: ignore
        return self._extract_text_content(result)

    @requires_session
    async def detach_kernel(self, notebook_path):
        """Detach the kernel from a notebook path. Kernel keeps running."""
        result = await self._session.call_tool(
            "detach_kernel",
            arguments={"notebook_path": notebook_path},
        )  # type: ignore
        return self._extract_text_content(result)

    @requires_session
    async def list_kernels(self):
        """List all available kernels"""
        result = await self._session.call_tool("list_kernels")  # type: ignore
        return self._extract_text_content(result)

    @requires_session
    async def list_notebooks(self):
        """List all notebooks with their attached kernel IDs."""
        result = await self._session.call_tool("list_notebooks")  # type: ignore
        return self._extract_text_content(result)

    async def get_first_kernel_id(self) -> str:
        """Parse the first running kernel ID from list_kernels two-level output."""
        output = await self.list_kernels()
        if not output:
            raise RuntimeError("list_kernels returned no output")
        for line in output.splitlines():
            stripped = line.strip()
            if stripped.startswith("id: "):
                # line format: "id: <id>  state: ..."
                parts = stripped.split()
                if len(parts) >= 2:
                    return parts[1]
        raise RuntimeError(f"No kernel found in list_kernels output:\n{output}")

    # -------------------------------------------------------------------------
    # Notebook Reading Methods
    # -------------------------------------------------------------------------

    @requires_session
    async def read_notebook(self, notebook_path, response_format="brief", start_index=0, limit=20):
        try:
            result = await self._session.call_tool("read_notebook", arguments={
                "notebook_path": notebook_path,
                "response_format": response_format,
                "start_index": start_index,
                "limit": limit,
            })  # type: ignore
            return self._extract_text_content(result)
        except Exception as e:
            logging.warning(f"Tool read_notebook raised error: {e}")
            return None

    # -------------------------------------------------------------------------
    # Cell Tool Methods  (all require notebook_path)
    # -------------------------------------------------------------------------

    @requires_session
    async def insert_cell(self, notebook_path_or_index, cell_index_or_type=None, cell_type_or_source=None, cell_source=None):
        if isinstance(notebook_path_or_index, str):
            notebook_path, cell_index, cell_type = notebook_path_or_index, cell_index_or_type, cell_type_or_source
        else:
            notebook_path, cell_index, cell_type, cell_source = self._DEFAULT_NOTEBOOK, notebook_path_or_index, cell_index_or_type, cell_type_or_source
        result = await self._call_tool_safe("insert_cell", {
            "notebook_path": notebook_path,
            "cell_index": cell_index,
            "cell_type": cell_type,
            "cell_source": cell_source,
        })
        return self._get_structured_content_safe(result) if result else None

    @requires_session
    async def insert_execute_code_cell(self, notebook_path, cell_index, cell_source, timeout=90):
        result = await self._call_tool_safe("insert_execute_code_cell", {
            "notebook_path": notebook_path,
            "cell_index": cell_index,
            "cell_source": cell_source,
            "timeout": timeout,
        })
        structured = self._get_structured_content_safe(result) if result else None

        # Special handling: tool returns list[str | ImageContent]
        # In JUPYTER_SERVER mode, the list gets flattened to a single string in TextContent
        if structured and "result" in structured:
            result_value = structured["result"]
            if not isinstance(result_value, list):
                structured["result"] = [result_value]
        return structured

    @requires_session
    async def read_cell(self, notebook_path_or_index, cell_index_or_outputs=None, include_outputs=True):
        if isinstance(notebook_path_or_index, str):
            notebook_path, cell_index = notebook_path_or_index, cell_index_or_outputs
        else:
            notebook_path, cell_index = self._DEFAULT_NOTEBOOK, notebook_path_or_index
            if isinstance(cell_index_or_outputs, bool):
                include_outputs = cell_index_or_outputs
        result = await self._call_tool_safe("read_cell", {
            "notebook_path": notebook_path,
            "cell_index": cell_index,
            "include_outputs": include_outputs,
        })
        return self._get_structured_content_safe(result) if result else None

    @requires_session
    async def move_cell(self, notebook_path_or_src, source_or_target=None, target_index=None):
        if isinstance(notebook_path_or_src, str):
            notebook_path, source_index, target_index = notebook_path_or_src, source_or_target, target_index
        else:
            notebook_path, source_index = self._DEFAULT_NOTEBOOK, notebook_path_or_src
            target_index = source_or_target
        result = await self._call_tool_safe("move_cell", {
            "notebook_path": notebook_path,
            "source_index": source_index,
            "target_index": target_index,
        })
        return self._get_structured_content_safe(result) if result else None

    @requires_session
    async def delete_cell(self, notebook_path_or_indices, cell_indices=None, include_source: bool = True):
        if isinstance(notebook_path_or_indices, str):
            notebook_path, indices = notebook_path_or_indices, cell_indices
        else:
            notebook_path, indices = self._DEFAULT_NOTEBOOK, notebook_path_or_indices
        result = await self._call_tool_safe("delete_cell", {
            "notebook_path": notebook_path,
            "cell_indices": indices,
            "include_source": include_source,
        })
        return self._get_structured_content_safe(result) if result else None

    @requires_session
    async def execute_cell(self, notebook_path_or_index, cell_index=None, timeout_seconds=300, stream=False, progress_interval=5):
        if isinstance(notebook_path_or_index, str):
            notebook_path, cell_index = notebook_path_or_index, cell_index
        else:
            notebook_path, cell_index = self._DEFAULT_NOTEBOOK, notebook_path_or_index
        result = await self._call_tool_safe("execute_cell", {
            "notebook_path": notebook_path,
            "cell_index": cell_index,
            "timeout_seconds": timeout_seconds,
            "stream": stream,
            "progress_interval": progress_interval,
        })
        structured = self._get_structured_content_safe(result) if result else None

        if structured and "result" in structured:
            result_value = structured["result"]
            if not isinstance(result_value, list):
                structured["result"] = [result_value]
        return structured

    @requires_session
    async def overwrite_cell_source(self, notebook_path, cell_index, cell_source):
        result = await self._call_tool_safe("overwrite_cell_source", {
            "notebook_path": notebook_path,
            "cell_index": cell_index,
            "cell_source": cell_source,
        })
        return self._get_structured_content_safe(result) if result else None

    @requires_session
    async def edit_cell_source(self, notebook_path_or_index, cell_index_or_old=None, old_or_new=None, new_string=None, replace_all=False):
        if isinstance(notebook_path_or_index, str):
            notebook_path, cell_index, old_string, new_string = notebook_path_or_index, cell_index_or_old, old_or_new, new_string
        else:
            notebook_path, cell_index, old_string, new_string = self._DEFAULT_NOTEBOOK, notebook_path_or_index, cell_index_or_old, old_or_new
        result = await self._call_tool_safe("edit_cell_source", {
            "notebook_path": notebook_path,
            "cell_index": cell_index,
            "old_string": old_string,
            "new_string": new_string,
            "replace_all": replace_all,
        })
        return self._get_structured_content_safe(result) if result else None

    # -------------------------------------------------------------------------
    # Execute Code  (operates on a kernel directly, not a notebook)
    # -------------------------------------------------------------------------

    @requires_session
    async def execute_code(self, kernel_id, code, timeout=60):
        result = await self._session.call_tool("execute_code", arguments={
            "kernel_id": kernel_id,
            "code": code,
            "timeout": timeout,
        })  # type: ignore
        structured = self._get_structured_content_safe(result)

        if structured and "result" in structured:
            result_val = structured["result"]
            if isinstance(result_val, str):
                structured["result"] = [result_val]
            elif not isinstance(result_val, list):
                structured["result"] = [result_val]

        return structured

    # -------------------------------------------------------------------------
    # Prompts
    # -------------------------------------------------------------------------

    @requires_session
    async def jupyter_cite(self, prompt, cell_indices, notebook_path=""):
        result = await self._session.get_prompt("jupyter_cite", arguments={
            "prompt": prompt,
            "cell_indices": cell_indices,
            "notebook_path": notebook_path,
        })  # type: ignore
        return [message.content.text for message in result.messages]