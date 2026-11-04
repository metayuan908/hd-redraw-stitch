import json,os,subprocess,sys,tempfile,unittest
from pathlib import Path
from PIL import Image,ImageDraw
ROOT=Path(__file__).resolve().parents[1];SCRIPTS=ROOT/'skills/hd-redraw-stitch/scripts';CLI=ROOT/'tests/fake_image_cli.py'
sys.path.insert(0,str(SCRIPTS))
from gpt_tile_redraw import default_tile_geometry
from tile_image import positions
class RouteTests(unittest.TestCase):
 def test_planner_avoids_duplicate_final_row(self):
  width,height,overlap_x,overlap_y=default_tile_geometry(3840,1984,True)
  self.assertLessEqual(width*height,1_200_000)
  self.assertEqual((len(positions(3840,width,overlap_x)),len(positions(1984,height,overlap_y))),(4,2))
  self.assertGreaterEqual(overlap_y,128)
 def test_architecture_without_semantic_masks_uses_anchor(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);Image.new('RGB',(128,96)).save(p/'source.png')
   base=[sys.executable,str(SCRIPTS/'plan_redraw_route.py'),str(p/'source.png'),
         '--content-mode','architecture','--target-width','256','--target-height','192',
         '--connected-subjects','--precise-geometry']
   route=json.loads(subprocess.run(base,capture_output=True,text=True,check=True).stdout)
   self.assertEqual(route['risk'],'high');self.assertEqual(route['route'],'coherent')
   route=json.loads(subprocess.run(base+['--semantic-masks'],capture_output=True,text=True,check=True).stdout)
   self.assertEqual(route['route'],'segmented')
 def run_route(self,route,p,*extra):
  env={**os.environ,'FAKE_IMAGE_LOG':str(p/'calls.jsonl')}
  args=[sys.executable,str(SCRIPTS/route),str(p/'source.png'),str(p/'out.png'),'--content-mode','scenery' if route=='gpt_tile_redraw.py' else 'architecture','--target-width','256','--target-height','192','--gpt-cli',str(CLI),'--work-dir',str(p/'job'),'--pilot-count','0','--alignment','none',*extra]
  return subprocess.run(args,capture_output=True,text=True,env=env)
 def test_tile_and_coherent_routes_use_durable_engine(self):
  for route in ['gpt_tile_redraw.py','gpt_coherent_redraw.py']:
   with self.subTest(route=route),tempfile.TemporaryDirectory() as d:
    p=Path(d);im=Image.new('RGB',(128,96),'#89a5bf');draw=ImageDraw.Draw(im);draw.rectangle((20,20,60,65),fill='white');im.save(p/'source.png')
    r=self.run_route(route,p,'--tile-width','128','--tile-height','128','--overlap-x','32','--overlap-y','32')
    self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertTrue((p/'out.png').is_file())
    if route=='gpt_coherent_redraw.py':
     progress=json.loads((p/'job/progress.json').read_text())
     self.assertEqual((progress['completed'],progress['total'],progress['percent']),(7,7,100))
     self.assertIsNone(progress['current_request_percent'])
    n=len((p/'calls.jsonl').read_text().splitlines());self.assertGreater(n,1)
    before=(p/'out.png').read_bytes();r=self.run_route(route,p,'--tile-width','128','--tile-height','128','--overlap-x','32','--overlap-y','32','--resume')
    self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertEqual(len((p/'calls.jsonl').read_text().splitlines()),n);self.assertEqual(before,(p/'out.png').read_bytes())
 def test_dry_run_never_generates(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);Image.new('RGB',(128,96)).save(p/'source.png');r=self.run_route('gpt_coherent_redraw.py',p,'--dry-run');self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertFalse((p/'calls.jsonl').exists())
if __name__=='__main__':unittest.main()
