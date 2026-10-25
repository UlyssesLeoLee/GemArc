import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from gemarc_mcp.paths import Workspace, GemArcError, read_json, digest
from gemarc_mcp import service

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.ws=Workspace(self.tmp.name)
        self.mock=patch.dict(os.environ,{'GEMARC_ALLOW_WRITE':'1'})
        self.mock.start()
    def tearDown(self):
        self.mock.stop()
        self.tmp.cleanup()
    def test_e2e_build_verify_export(self):
        self.assertEqual(service.init_project(self.ws,'demo')['status'],'ok')
        built=service.build(self.ws,'demo')
        self.assertEqual(built['status'],'ok')
        self.assertFalse(built['viewer3D'])
        result=service.verify(self.ws,'demo')
        self.assertEqual(result['status'],'ok')
        done=service.export_zip(self.ws,'demo')
        self.assertTrue(Path(done['archive']).is_file())
        self.assertEqual(len(done['sha256']),64)
    def test_do_not_overwrite_new_project(self):
        service.init_project(self.ws,'demo')
        with self.assertRaises(GemArcError): service.init_project(self.ws,'demo')
    def test_reject_traversal(self):
        with self.assertRaises(GemArcError): self.ws.path('../foo')
    def test_reject_absolute_outside(self):
        with self.assertRaises(GemArcError): self.ws.path(Path(self.tmp.name).parent)
    def test_read_only(self):
        with patch.dict(os.environ,{'GEMARC_ALLOW_WRITE':'0'}):
            with self.assertRaises(GemArcError): service.init_project(self.ws,'deny')
    def test_optimistic_replace(self):
        service.init_project(self.ws,'demo')
        file,m=service.load_model(self.ws,'demo')
        with self.assertRaises(GemArcError): service.replace_model(self.ws,'demo',m,'bad-sha')
        m['meta']['description']='更新成功'
        result=service.replace_model(self.ws,'demo',m,digest(file))
        self.assertEqual(result['status'],'ok')
        self.assertEqual(read_json(file)['meta']['description'],'更新成功')
    def test_invalid_replace_keeps_data(self):
        service.init_project(self.ws,'demo')
        file,m=service.load_model(self.ws,'demo')
        before=digest(file)
        m['edges'][0]['targetId']='missing'
        self.assertEqual(service.replace_model(self.ws,'demo',m,before)['status'],'invalid')
        self.assertEqual(digest(file),before)
    def test_diff(self):
        service.init_project(self.ws,'alpha'); service.init_project(self.ws,'beta')
        result=service.diff(self.ws,'alpha','beta')
        self.assertEqual(result['status'],'ok')
    def test_scan_no_symlinks(self):
        path=self.ws.path('repo');path.mkdir()
        (path/'main.py').write_text('print(1)',encoding='utf8')
        result=service.inspect_path(self.ws,'repo')
        self.assertEqual(result['scannedFiles'],1)
        self.assertEqual(result['candidateFacts'][0]['verification'],'candidate')
    def test_reject_invalid_dist(self):
        service.init_project(self.ws,'demo')
        with self.assertRaises(GemArcError):service.build(self.ws,'demo','../outside')

if __name__=='__main__': unittest.main()
