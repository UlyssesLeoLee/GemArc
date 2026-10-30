"""SDK-free decorator/registration smoke test; not a real MCP handshake."""
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from gemarc_mcp import server

class MCPRegistrationTests(unittest.TestCase):
    def _register(self, toolset):
        class FakeServer:
            def __init__(self,*args,**kwargs):
                self.tools=[]; self.resources=[]; self.prompts=[]; self.kwargs=kwargs
            def tool(self,name,**kwargs):
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
        tmod=types.ModuleType('mcp.types')
        for attr in ('CallToolResult','TextContent','ToolAnnotations'):
            setattr(tmod,attr,lambda **kw: kw)
        with patch.dict(sys.modules,{'mcp':mod,'mcp.server':smod,'mcp.types':tmod}):
            return server.create_server(toolset)

    def test_register_all_tools(self):
        app=self._register('full')
        self.assertEqual(len(app.tools),16)
        self.assertEqual(len(set(app.tools)),16)
        self.assertEqual(len(app.resources),2)
        self.assertEqual(len(app.prompts),1)
        # subscriptions/listen would answer a POST with a never-ending SSE stream (pins a serverless invocation)
        self.assertIs(app.kwargs.get('subscriptions'),False)
        for name in ('gemarc_scaffold','gemarc_contracts','gemarc_brief','gemarc_render','gemarc_new_model'):
            self.assertIn(name, app.tools)

    def test_stateless_toolset_registers_only_model_in_tools(self):
        app=self._register('stateless')
        self.assertEqual(len(app.tools),8)
        self.assertNotIn('gemarc_build', app.tools)
        self.assertEqual(server.WRITE_TOOLS,set())

class MainModuleTests(unittest.TestCase):
    def test_importing_the_main_module_does_not_start_a_server(self):
        """`python -m gemarc_mcp` runs it as __main__; pydoc / walk_packages merely import it and must not block."""
        src = Path(server.__file__).resolve().parent.parent
        code = f"import sys; sys.path.insert(0, {str(src)!r}); import gemarc_mcp.__main__; print('imported')"
        # stdin stays open: a server that was started would wait on it forever
        proc = subprocess.Popen([sys.executable, '-I', '-c', code], stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            proc.wait(timeout=60)
        except subprocess.TimeoutExpired:
            self.fail('importing gemarc_mcp.__main__ started the stdio server and blocked')
        finally:
            if proc.poll() is None:
                proc.kill()
            out, _ = proc.communicate()
        self.assertIn('imported', out)

if __name__=='__main__':unittest.main()
