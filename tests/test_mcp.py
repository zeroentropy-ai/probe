"""Tests for MCP server tool definitions."""

import pytest
from probe.mcp.server import create_mcp_server


class TestMCPServer:
    def test_server_has_tools(self):
        server = create_mcp_server()
        assert server is not None

    def test_server_name(self):
        server = create_mcp_server()
        assert server.name == "probe"
