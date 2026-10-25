"""SDK-free decorator/registration smoke test; not a real MCP handshake."""
import sys
import types
import unittest
from unittest.mock import patch
from gemarc_mcp import server

class MCPRegistrationTests(unittest.TestCase):
    def test_register_all_tools(self):
        class FakeServer:
            def __init__(self,*args,**kwargs):
                self.tools=[]; self.resources=[]; self.prompts=[]
            def tool(self,name):
                def deco(fn): self.tools.append(name);return fn
                return deco
            def resource(self,uri):
                def deco(fn): self.resources.append(uri);return fn
                return deco
            def prompt(self,name):
                def deco(fn): self.prompts.append(name);return fn
                return deco
        mod=types.ModuleType('mcp')
        smod=types.ModuleType('mcp.server')
        smod.MCPServer=FakeServer
        with patch.dict(sys.modules,{'mcp':mod,'mcp.server':smod}):
            app=server.create_server()
        self.assertEqual(len(app.tools),13)
        self.assertEqual(len(set(app.tools)),13)
        self.assertEqual(len(app.resources),2)
        self.assertEqual(len(app.prompts),1)
        for name in ('gemarc.scaffold','gemarc.contracts'):
            self.assertIn(name, app.tools)

if __name__=='__main__':unittest.main()
