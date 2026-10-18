"""Only approved port tails, retained masks and their immediate boundaries."""
import copy
import csv
import io
import json
import unittest
import xml.etree.ElementTree as E

import geom
from scene import Scene, Drop
from test_elution_recovery_local import source, ROOT, PROJECT, cells
from update_port_recovery_local import transform, F_TARGETS
from waste_recovery import waste_path, waste_relays, boxes


class PortRecoveryTests(unittest.TestCase):
    def test_f_geometry_timing_and_full_port_overlap(self):
        for w,target,seconds,count in [(16,8,61,23),(12,6,55,21)]:
            for hx in geom.HOLE_X.values():
                path=waste_path((hx-w//2,172,w,16),hx)
                self.assertEqual(len(path),count)
                self.assertEqual(path[-1],(hx-target//2,186,target,6))
                for i,rc in enumerate(path):
                    x,y,W,H=rc
                    self.assertTrue(0<=x<x+W<=256 and 0<=y<y+H<=192)
                    if i>=4:
                        self.assertTrue(x<=hx-1 and x+W>=hx+1 and y<=189.5 and y+H>=191.5)
                    if i:
                        prev=path[i-1]
                        for axis in (0,1):
                            self.assertLessEqual(abs(prev[axis]-rc[axis]),1)
                            self.assertLessEqual(abs(prev[axis]+prev[axis+2]-rc[axis]-rc[axis+2]),1)
                        self.assertLessEqual(len(cells(rc)),len(cells(prev)))
                relays=waste_relays([path[0]],[hx],['(28,15)(4,8)'])
                self.assertEqual(sum(r.dur for r in relays)//1000,seconds)
                self.assertTrue(all('(28,15)(4,8)' in r.codes for r in relays))
                self.assertTrue(relays[-1].codes)

    def test_generator_flush_preserves_other_drops_and_actual_volumes(self):
        import waste_grid
        for width,total in [(16,12.),(12,9.6)]:
            S=Scene();S.manual_aspirations=[]
            resident=S.add(Drop('L1',(15,28,8,4),1.6))
            b=S.add(Drop('BLOB1',(54-width//2,172,width,16),total))
            b.electrode_area=width*16;b.margin_budget=width*16-geom.cells(total)
            blobs={1:b}
            waste_grid.flush(S,blobs)
            self.assertEqual(len(S.manual_aspirations),1)
            relay,info=S.manual_aspirations[0]
            self.assertEqual(info['volume_ul'],total)
            self.assertEqual(info['rects'],[list(waste_path((54-width//2,172,width,16),54)[-1])])
            self.assertTrue(info['hole_overlap_recovery'])
            self.assertTrue(all(geom.emit(resident.rect) in r.codes for r in S.relays))
            self.assertEqual(S.relays[-1].codes,[geom.emit(resident.rect)])
            self.assertIsNone(blobs[1])
            self.assertEqual(list(S.drops),['L1'])

    def test_native_patch_only_appends_f_tails_and_changes_s_endpoint(self):
        text=source('PTseq_16_A.elab');layout=source('PTseq_16_A.elab.layout')
        main=source('main.elab');mapping=json.loads(source('步骤编号映射_20261016.json'))
        rows=list(csv.DictReader(io.StringIO(source('手工吸液顺序.csv'))))
        result=transform(text,layout,main,mapping,copy.deepcopy(rows))
        new,lt,mt,_,new_rows,sreport,report=result
        self.assertEqual(report['total_nominal_change_seconds'],578)
        self.assertEqual(int(E.fromstring(mt)[0].get('Timeout')),int(E.fromstring(main)[0].get('Timeout'))+578)
        before={f.get('ID'):f for f in E.fromstring(text)}
        after={f.get('ID'):f for f in E.fromstring(new)}
        selected={str(v[0]) for v in F_TARGETS.values()}|{'2032','2055','4022','4035'}
        for fid,f in before.items():
            if fid not in selected:self.assertEqual(E.tostring(f),E.tostring(after[fid]))
            else:
                self.assertEqual([c.attrib for c in f[:-1]],[c.attrib for c in after[fid][:-1]])
                self.assertTrue(all(E.tostring(a)==E.tostring(b) for a,b in zip(f[:-1],after[fid][:-1])))
        for label,(fid,cid) in F_TARGETS.items():
            old=before[str(fid)][-1];newc=after[str(fid)][-1]
            self.assertEqual(newc.get('ID'),str(cid))
            self.assertEqual([r.attrib for r in old],[r.attrib for r in list(newc)[:len(old)]])
            self.assertEqual(old.get('PreTask'),newc.get('PreTask'))
            self.assertTrue(boxes(newc[-1].get('RelayCode','')))
        for a,b in zip(rows,new_rows):
            self.assertEqual(a['吸液孔'],b['吸液孔'])
            self.assertEqual(a['本次总液体µL（16道合计；排珠载液不计文库）'],b['本次总液体µL（16道合计；排珠载液不计文库）'])
        E.fromstring(lt)

    def test_released_native_f_tails_match_generated_frames(self):
        fs={f.get('ID'):f for f in E.parse(ROOT/PROJECT/'PTseq_16_A.elab').getroot()}
        original={f.get('ID'):f for f in E.fromstring(source('PTseq_16_A.elab'))}
        rows=list(csv.DictReader(io.StringIO(source('手工吸液顺序.csv'))))
        for label,(fid,cid) in F_TARGETS.items():
            row=next(r for r in rows if r['吸液流程组编号']==str(fid))
            rects=boxes(row['待吸液电极']);ports=[int(k[1:]) for k in row['吸液孔'].split('/')]
            old=original[str(fid)][-1];new=fs[str(fid)][-1]
            retained=[geom.emit(rc) for rc in boxes(old[-1].get('RelayCode')) if rc not in rects]
            plan=waste_relays(rects,[geom.HOLE_X[k] for k in ports],retained)
            self.assertEqual([r.attrib for r in old],[r.attrib for r in list(new)[:len(old)]])
            self.assertEqual([r.attrib for r in list(new)[len(old):]],
                [E.fromstring(r.xml(i)).attrib for i,r in enumerate(plan[1:],len(old)+1)])


if __name__=='__main__':unittest.main()
