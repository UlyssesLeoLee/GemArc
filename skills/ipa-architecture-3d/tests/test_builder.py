import importlib.util
import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0,str(ROOT/'scripts'))
from build import run,localize_model
from validate import validate
from sync_version import sync
MODEL=ROOT/'assets/examples/illustrative-architecture.json'

class ArchitectureTests(unittest.TestCase):
 def setUp(self):self.model=json.loads(MODEL.read_text(encoding='utf8'))
 def test_languages_and_12_views(self):
  for language in ('zh-CN','ja-JP','en-US'):
   with self.subTest(lang=language):
    errs,warns=validate(self.model,False,language)
    self.assertEqual(errs,[]);self.assertEqual(warns,[])
    localized=localize_model(self.model,language)
    self.assertEqual(localized['meta']['language'],language)
    self.assertEqual(len(localized['views']),12)
    self.assertEqual({n['id'] for n in localized['nodes']},{n['id'] for n in self.model['nodes']})
    self.assertTrue(all(v['reviewIntent'] for v in localized['views']))
    self.assertFalse(any('i18n' in v for v in localized['views']))
 def test_build_generates_real_threejs_project(self):
  with tempfile.TemporaryDirectory() as temp:
   path=Path(temp)
   for language in ('zh-CN','ja-JP','en-US'):
    out=path/language
    self.assertEqual(run([str(MODEL),'--out',str(out),'--lang',language]),0)
    self.assertIn("from 'three'",(out/'src/main.js').read_text(encoding='utf8'))
    self.assertIn('<html lang="'+language+'">',(out/'index.html').read_text(encoding='utf8'))
    self.assertEqual(json.loads((out/'viewer-config.json').read_text(encoding='utf8'))['language'],language)
    self.assertTrue((out/'tests/layout.test.mjs').exists())
 def test_unsafe_or_incomplete_architecture_is_not_strict(self):
  self.assertTrue(validate(self.model,True,'zh-CN')[0])
  self.model['nodes'][0]['verification']='confirmed'
  self.model['views'][0]['dimensions']['y']['field']=self.model['views'][0]['dimensions']['x']['field']
  self.assertTrue(validate(self.model,False,'zh-CN')[0])
 def test_no_missing_language_can_be_silently_selected(self):
  self.assertTrue(validate(self.model,False,'fr-FR')[0])
  with tempfile.TemporaryDirectory() as temp:
   self.assertEqual(run([str(MODEL),'--out',str(Path(temp)/'fr'),'--lang','fr-FR']),1)

 def test_every_manifest_agrees_on_the_version(self):
  # The version used to be hand-written in five places, so the plugin could ship a
  # manifest that lied about its own contents. One file is authoritative now.
  self.assertEqual(sync(check_only=True),[])

 def test_viewer_config_only_declares_what_the_viewer_reads(self):
  # build.py writes renderer/flatRenderer/isSample that nothing consumes. Shipping
  # config nobody reads invites the next person to trust a flag that does nothing.
  line=(ROOT/'scripts'/'build.py').read_text(encoding='utf8').split('viewer-config.json')[1].split('\n')[0]
  main=(ROOT/'assets'/'viewer'/'src'/'main.js').read_text(encoding='utf8')
  for key in re.findall(r"'([A-Za-z]+)':",line):
   with self.subTest(key=key):
    self.assertIn(f'config.{key}',main,
      f'viewer-config declares {key} but main.js never reads it')

 def test_every_manifest_declares_the_same_license(self):
  # A distributed artifact whose manifests disagree about licensing is a legal
  # problem, not a cosmetic one. LICENSE and the NOTICE file must agree with them.
  license_text=(ROOT.parents[1]/'LICENSE').read_text(encoding='utf8')
  self.assertIn('MIT License',license_text)
  notice=(ROOT.parents[1]/'THIRD_PARTY_NOTICES.md').read_text(encoding='utf8')
  for dep in ('three','vite'):
   self.assertIn(dep,notice,f'{dep} missing from THIRD_PARTY_NOTICES.md')
  for manifest in (ROOT.parents[1]/'plugin.json',
                   ROOT.parents[1]/'.claude-plugin'/'plugin.json',
                   ROOT.parents[1]/'.claude-plugin'/'marketplace.json',
                   ROOT/'assets'/'viewer'/'package.json'):
   with self.subTest(manifest=manifest.name):
    doc=json.loads(manifest.read_text(encoding='utf8'))
    nodes=[doc] if 'license' in doc else doc.get('plugins',[doc])
    for node in nodes:
     self.assertEqual(node.get('license'),'MIT',f'{manifest.name}: license must be MIT')

if __name__=='__main__':unittest.main()
