"""20261015 waste-only checks; real Scene validation, no protocol/GUI/instrument."""
import unittest
from unittest.mock import patch

import contour
import geom
import ops
import rigid
import waste_grid as W
from scene import Drop, Scene


class RevisionWasteTests(unittest.TestCase):
    def run_waste(self,n,*,supernatant=False,resident=0,single_stop=False):
        scene=Scene();scene.manual_aspirations=[];items=[];lanes=[];blobs={}
        for r in range(1,17):
            ln=ops.Lane(1,r);lanes.append(ln)
            vol=round(n*geom.CELL_UL,2)
            if vol in (5.,5.9):rc=geom.lane_target(1,r,vol,'right')
            else:
                width,height=geom.lane_shape(vol);rc=(ln.X0+12 if resident else ln.X0+2,ln.Y0,width,height)
                if n==12:rc=(rc[0],rc[1],3,4)
            d=scene.add(Drop(ln.name,rc,vol));items.append((ln,d))
            if resident:
                w,h=geom.lane_shape(round(resident*.05,2));rc=(ln.X0,ln.Y0,w,h)
                keep=scene.add(Drop('KEEP'+ln.id,rc,round(resident*geom.CELL_UL,2)))
        expected=(n+resident)*16*.05;previous={};parallel=[False];original=Scene._record

        def record(s,*args,**kwargs):
            duration=args[0] if args else kwargs.get('dur',geom.STEP_MS)
            original(s,*args,**kwargs)
            current={id(d):(d,d.target_cells(),d.rect) for d in s.drops.values() if d.rects()}
            positive=[cells for d,cells,rc in current.values() if d.vol>0]
            self.assertEqual(len(frozenset().union(*positive)),sum(map(len,positive)))
            aspirated=sum(info['volume_ul'] for relay,info in s.manual_aspirations)
            self.assertAlmostEqual(sum(d.vol for d,cells,rc in current.values())+aspirated,expected)
            moved=0
            for key,(d,cells,rc) in current.items():
                self.assertEqual(len(cells),geom.cells(d.vol)+d.margin_budget);self.assertTrue(contour.connected(cells))
                for x,y in cells:
                    if y<W.TRANSPORT_Y:
                        self.assertTrue(46<=x<50 or any(ln.X0<=x<ln.X0+geom.LANE_L
                            and geom.physical_zone(ln.c,ln.r)[1]<=y<geom.physical_zone(ln.c,ln.r)[1]+6 for ln in lanes),(d.name,x,y))
                if key not in previous:continue
                _,old,old_rc=previous[key]
                if old==cells:continue
                if len(old)!=len(cells):continue  # 整排并入既有BLOB，逐帧总量检查已覆盖。
                dx,dy=rc[0]-old_rc[0],rc[1]-old_rc[1]
                if cells==frozenset((x+dx,y+dy) for x,y in old) and rc[2:]==old_rc[2:]:
                    self.assertLessEqual(abs(dx)+abs(dy),1,(d.name,old_rc,rc));moved+=1
                else:
                    events=[e for e in getattr(s,'waste_morph_events',[]) if e['name']==d.name
                            and e['frame']==s.nsteps-1]
                    self.assertTrue(events,(d.name,'unrecorded morph'))
                    self.assertEqual(events[-1]['area'],len(cells));self.assertGreaterEqual(duration,4000)
            if moved>1:parallel[0]=True
            for event in getattr(s,'split_events',[]):
                if event['partition_frame']!=s.nsteps-1:continue
                parent,old,rc=previous[event['_parent_key']]
                children=[current[k] for k in event['_child_keys']]
                self.assertEqual(frozenset().union(*(q[1] for q in children)),old)
                areas=[len(q[1]) for q in children]
                self.assertLess(max(event['volumes'])-min(event['volumes']),1e-8)
                self.assertEqual(sum(areas),len(old))
                self.assertEqual(event['axis'],'x');self.assertGreaterEqual(rc[2],rc[3])
            previous.clear();previous.update(current)

        with patch.object(Scene,'_record',record):
            scene.step(changed=[])
            if supernatant:rigid.supernatant_bites(scene,lanes,blobs,0,collect_three=n==100)
            else:rigid.egress_pieces(scene,items,blobs,single_stop=single_stop,collect_a05=n==30)
        self.assertFalse(scene.waste_pool.items)
        self.assertTrue(all(d is None for d in blobs.values()))
        self.assertAlmostEqual(sum(i['volume_ul'] for _,i in scene.manual_aspirations),n*16*.05)
        self.assertTrue(parallel[0],'row movement should retain parallel progress')
        for relay,info in scene.manual_aspirations:
            self.assertLessEqual(len(info['port_volumes']),4)
            self.assertTrue(set(info['port_volumes'])<=set('F'+str(k) for k in W.PORTS))
            for rc in info['rects']:
                self.assertEqual(rc[1]+rc[3],W.DOCK_BOTTOM)
                self.assertLessEqual(max(rc[2:]),2.5*min(rc[2:]))
                self.assertLessEqual(rc[2],40);self.assertLessEqual(rc[3],W.DOCK_BOTTOM-W.TRANSPORT_Y)
        for e in getattr(scene,'split_events',[]):
            self.assertTrue(all(0<=b-a<=1 for a,b in zip(e['gaps'],e['gaps'][1:])))
        return scene

    def test_a05_waste_preserves_resident_half_microlitre(self):
        self.run_waste(30,resident=10)

    def test_r04_balanced_five_microlitres(self):
        scene=self.run_waste(100,supernatant=True)
        pairs={tuple(e['volumes']) for e in scene.split_events}
        self.assertEqual(pairs,{(2.5,2.5),(1.25,1.25),(.625,.625)})
        self.assertEqual([info['volume_ul'] for relay,info in scene.manual_aspirations],[40.,40.])
        self.assertTrue(all(d.magnet_only and d.vol==0 for d in scene.drops.values()))
        self.assertTrue(all(e['tile_area']==e['tile_shape'][0]*e['tile_shape'][1] for e in scene.waste_grid_events))

    def test_r06_three_microlitre_carrier(self):
        self.run_waste(60)

    def test_b05_excess_barcode_preserves_other_half(self):
        self.run_waste(12,resident=12)

    def test_c04_balanced_118_cells_without_tiny_fragments(self):
        scene=self.run_waste(118,supernatant=True)
        pairs={tuple(e['volumes']) for e in scene.split_events}
        self.assertEqual(pairs,{(2.95,2.95),(1.475,1.475),(.7375,.7375)})
        self.assertGreaterEqual(min(e['tile_area'] for e in scene.waste_grid_events),14)
        self.assertTrue(all(e['tile_area']==16 and e['tile_shape']==[4,4] for e in scene.waste_grid_events))

    def test_c06_three_microlitre_carrier_one_capacity_checked_stop(self):
        scene=self.run_waste(60,single_stop=True)
        self.assertEqual(len(scene.manual_aspirations),1)
        self.assertEqual(len(scene.manual_aspirations[0][1]['port_volumes']),4)

    def test_prime_packets_remain_exact_and_connected(self):
        for n in (13,15,29,59):
            rc=(10,29,(n+3)//4,4);cells=W._compact_cells(n,rc)
            self.assertEqual(len(cells),n);self.assertTrue(contour.near_rectangle(cells))
            self.assertEqual(contour.bounds(cells),rc)

    def test_capacity_failure_precedes_any_mutation(self):
        scene=Scene();items=[]
        for r in range(1,17):
            ln=ops.Lane(1,r);items.append((ln,scene.add(Drop(ln.name,(12,geom.physical_zone(1,r)[1]+1,20,5),5))))
        before={d.name:d.target_cells() for ln,d in items}
        with self.assertRaisesRegex(AssertionError,'一次装入'):
            W.bites(scene,items,{},single_stop=True)
        self.assertEqual(scene.nsteps,0)
        self.assertEqual({d.name:d.target_cells() for ln,d in items},before)

    def test_zero_liquid_bead_waste_stays_rejected(self):
        scene=Scene();ln=ops.Lane(1,1);d=scene.add(Drop(ln.name,(36,ln.Y0,3,4),0));d.magnet_only=True
        with self.assertRaisesRegex(AssertionError,'真实载液'):
            W.egress_pieces(scene,[(ln,d)],{})


if __name__=='__main__':unittest.main()
