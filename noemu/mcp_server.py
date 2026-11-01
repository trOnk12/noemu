"""Official MCP SDK server; serialized tool calls share the CLI command model."""
from __future__ import annotations

import sys
import json
from importlib.metadata import PackageNotFoundError, version

import anyio
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from noemu.protocol import MAX_MESSAGE_BYTES, NoemuError


class BoundedInput:
    """Bound memory before the SDK parses JSON. Discard an oversized line."""
    def __init__(self, stream):
        # Decode after framing: TextIOWrapper can raise on invalid UTF-8 before
        # the SDK sees a request, terminating the whole server task group.
        self._source = stream  # Keep the wrapper alive; it owns the binary buffer.
        self.stream = getattr(stream, "buffer", stream)

    def __aiter__(self):
        return self

    async def __anext__(self):
        line = await anyio.to_thread.run_sync(self.stream.readline, MAX_MESSAGE_BYTES + 1)
        if not line:
            raise StopAsyncIteration
        if len(line) > MAX_MESSAGE_BYTES:
            while line and not line.endswith(b"\n"):
                line = await anyio.to_thread.run_sync(self.stream.readline, MAX_MESSAGE_BYTES + 1)
            # Pass a parse failure to the SDK, which reports it and continues.
            return "{\n"
        try:
            return line.decode("utf-8")
        except UnicodeDecodeError:
            return "{\n"


class BoundedOutput:
    """Bound the SDK's complete serialized envelope, including duplicated evidence."""
    def __init__(self, stream):
        self.stream = stream

    async def write(self, message: str):
        if len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
            # This is SDK-generated JSON, not a second request parser. Preserve
            # correlation when possible, but never echo an unbounded request ID.
            envelope = json.loads(message)
            error = {"jsonrpc": "2.0", "id": envelope.get("id"), "error": {
                "code": -32603,
                "message": "noemu: response exceeds 1 MiB; operation may have completed; inspect state before retrying",
                "data": {"code": "message_too_large", "status": "inconclusive"}}}
            message = json.dumps(error, ensure_ascii=True) + "\n"
            if len(message.encode("utf-8")) > MAX_MESSAGE_BYTES:
                error["id"] = None
                message = json.dumps(error, ensure_ascii=True) + "\n"
        return await self.stream.write(message)

    async def flush(self):
        await self.stream.flush()


async def serve() -> None:
    from noemu import cli

    try:
        package_version = version("noemu")
    except PackageNotFoundError:
        package_version = "0+uninstalled"
    server = Server("noemu", version=package_version)
    lock = anyio.Lock()

    @server.list_tools()
    async def list_tools():
        return [types.Tool(name=name, description=description, inputSchema={
            "type": "object", "properties": properties, "required": required,
            "additionalProperties": False,
        }) for name, (description, properties, required) in cli.TOOLS.items()]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        async with lock:
            try:
                # Do not abandon a mutating command when its client cancels.
                # The lock stays held until its thread has actually finished.
                output = await anyio.to_thread.run_sync(cli.run_tool, name, arguments)
                return types.CallToolResult(content=output.content,
                                            structuredContent=output.result.data if output.result else None,
                                            isError=False)
            except (RuntimeError, OSError, ValueError, KeyError) as error:
                structured = error.result if isinstance(error, NoemuError) else None
                return types.CallToolResult(content=[types.TextContent(type="text", text=str(error))],
                                            structuredContent=structured, isError=True)

    async with stdio_server(stdin=BoundedInput(sys.stdin), stdout=BoundedOutput(anyio.wrap_file(sys.stdout))) as (read, write):
        await server.run(read, write, server.create_initialization_options())


def run() -> int:
    anyio.run(serve)
    return 0
