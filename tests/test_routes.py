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
 def test_4k_planner_reduces_requests_only_with_adequate_overlap(self):
  for structural in (False,True):
   with self.subTest(structural=structural):
    default=default_tile_geometry(3840,2160,structural)
    self.assertGreaterEqual(len(positions(3840,default[0],default[2]))*
                            len(positions(2160,default[1],default[3])),9)
    width,height,overlap_x,overlap_y=default_tile_geometry(3840,2160,structural,
                                                           allow_narrower_overlap=True)
    self.assertLessEqual(width*height,1_200_000)
    self.assertEqual((len(positions(3840,width,overlap_x)),len(positions(2160,height,overlap_y))),(4,2))
    self.assertEqual((overlap_x,overlap_y),(112,112))
 def test_observed_capacity_keeps_wider_overlap_with_eight_tiles(self):
  width,height,overlap_x,overlap_y=default_tile_geometry(3840,2160,True,1_258_248)
  self.assertEqual((len(positions(3840,width,overlap_x)),len(positions(2160,height,overlap_y))),(4,2))
  self.assertEqual((overlap_x,overlap_y),(128,128))
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
    options=['--tile-width','128','--tile-height','128','--overlap-x','32','--overlap-y','32']
    if route=='gpt_coherent_redraw.py':
     options+=['--alignment','translation']
    r=self.run_route(route,p,*options)
    self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertTrue((p/'out.png').is_file())
    if route=='gpt_coherent_redraw.py':
     progress=json.loads((p/'job/progress.json').read_text())
     self.assertEqual((progress['completed'],progress['total'],progress['percent']),(7,7,100))
     self.assertIsNone(progress['current_request_percent'])
     mask_path=next((p/'job/tiles/engine/inputs').glob('001-*/*/mask.png'))
     with Image.open(mask_path) as mask:
      self.assertEqual(mask.size,(180,180))
      self.assertEqual(mask.getpixel((0,0))[3],255)
      self.assertEqual(mask.getpixel((26,26))[3],0)
    n=len((p/'calls.jsonl').read_text().splitlines());self.assertGreater(n,1)
    before=(p/'out.png').read_bytes();r=self.run_route(route,p,*options,'--resume')
    self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertEqual(len((p/'calls.jsonl').read_text().splitlines()),n);self.assertEqual(before,(p/'out.png').read_bytes())
 def test_dry_run_never_generates(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);Image.new('RGB',(128,96)).save(p/'source.png');r=self.run_route('gpt_coherent_redraw.py',p,'--dry-run');self.assertEqual(r.returncode,0,r.stdout+r.stderr);self.assertFalse((p/'calls.jsonl').exists())
 def test_architecture_pilot_uses_local_images_from_different_rows(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);Image.new('RGB',(128,96),'#89a5bf').save(p/'source.png')
   r=self.run_route('gpt_coherent_redraw.py',p,'--tile-width','96','--tile-height','80',
                    '--overlap-x','16','--overlap-y','16','--pilot-count','2')
   self.assertEqual(r.returncode,0,r.stdout+r.stderr)
   calls=[json.loads(line) for line in (p/'calls.jsonl').read_text().splitlines()]
   self.assertEqual([call['images'] for call in calls],[1,1,1])
   tiles=p/'job/tiles'
   regions=json.loads((tiles/'regions.json').read_text())['regions']
   pilots=[region for region in regions if region['pilot']]
   self.assertEqual(len(pilots),2)
   self.assertNotEqual(pilots[0]['crop_bbox'][1],pilots[1]['crop_bbox'][1])
   self.assertFalse((p/'out.png').exists())
if __name__=='__main__':unittest.main()
