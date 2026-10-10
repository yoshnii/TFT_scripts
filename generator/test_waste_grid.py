"""底部原形排废离线测试；不操作GUI/仪器。"""
import unittest
from unittest.mock import patch
import contour,geom,ops,rigid,waste_grid
from scene import Drop,Scene

class WasteGridTests(unittest.TestCase):
    def test_layout_uses_original_vertical_dimensions(self):
        for shape,expected in [((2,5),(8,2)),((2,6),(8,2)),((1,4),(8,2)),((3,6),(8,2)),((4,4),(4,4))]:
            self.assertEqual(waste_grid.layout(*shape,16),expected)
            self.assertEqual(len(waste_grid.plan_batch(*shape,16)),1)
        for shape in ((4,5),(4,6)):
            self.assertIsNone(waste_grid.layout(*shape,16))
            plans=waste_grid.plan_batch(*shape,16)
            self.assertEqual([p['count'] for p in plans],[8,8])
            self.assertEqual([p['columns'] for p in plans],[4,4])
            self.assertEqual([p['rows'] for p in plans],[2,2])
        self.assertGreaterEqual(waste_grid.TRANSPORT_Y,23+149)

    def test_flush_only_translates_different_size_rectangles(self):
        S=Scene();S.manual_aspirations=[];blobs={k:None for k in waste_grid.PORTS}
        for k,rc,vol in [(4,(212,184,4,5),1.0),(3,(158,183,6,6),1.8)]:
            blobs[k]=S.add(Drop(f'BLOB{k}',rc,vol))
        S.step(changed=[])
        with patch.object(rigid,'reshape',side_effect=AssertionError('取液终态不允许整形')):
            waste_grid.flush(S,blobs)
        info=S.manual_aspirations[-1][1]
        self.assertEqual([r[2:] for r in info['rects']],[[4,5],[6,6]])
        self.assertAlmostEqual(info['volume_ul'],2.8)
        self.assertTrue(all(d is None for d in blobs.values()))

    def test_send_preserves_two_by_five_and_avoids_other_tecs(self):
        S=Scene();S.manual_aspirations=[];items=[];original=Scene._record
        for row in range(1,9):
            lane=ops.Lane(1,row)
            items.append((lane,S.add(Drop(f'WG{row}',(44,lane.Y0,2,6 if row%2 else 5),.5))))
        S.step(changed=[])
        def trace(scene,*args,**kw):
            original(scene,*args,**kw)
            for d in scene.drops.values():
                if d.name.startswith('WG'):
                    self.assertEqual(contour.bounds(d.target_cells())[2:],(2,5))
                for x,y in d.target_cells():
                    self.assertFalse(23<=y<172 and any(start<=x<start+18 for start in (50,90,130,170,210)))
        with patch.object(Scene,'_record',trace),patch.object(rigid,'reshape',side_effect=AssertionError('不得旋转废液块')):
            waste_grid.send(S,items,geom.gap_x(1),{k:None for k in waste_grid.PORTS},final=True)
        self.assertEqual(len(S.waste_grid_events),8)
        self.assertTrue(all(e['tile_shape']==[2,5] for e in S.waste_grid_events))
        self.assertAlmostEqual(sum(info['volume_ul'] for relay,info in S.manual_aspirations),4.0)
        self.assertEqual(len(S.manual_aspirations),1)
        self.assertEqual(S.manual_aspirations[0][1]['hole'],'F1')
        self.assertEqual(S.manual_aspirations[0][1]['rects'],[[50,179,8,10]])

    def test_cdna_waste_is_one_channel_wide_packet_and_one_remainder(self):
        S=Scene();S.manual_aspirations=[];items=[]
        for row in range(1,17):
            lane=ops.Lane(1,row)
            items.append((lane,S.add(Drop(f'WASTE{row}',(34,lane.Y0,6,5),1.5))))
        S.step(changed=[])
        waste_grid.bites(S,items,{k:None for k in waste_grid.PORTS})
        shapes=[tuple(e['tile_shape']) for e in S.waste_grid_events]
        self.assertEqual(shapes.count((4,5)),16)
        self.assertEqual(shapes.count((2,5)),16)
        self.assertEqual(len(shapes),32)
        self.assertAlmostEqual(sum(info['volume_ul'] for relay,info in S.manual_aspirations),24.0)
        self.assertEqual([info['hole'] for relay,info in S.manual_aspirations],['F2/F1','F1'])
        self.assertEqual([b['minimum_ports'] for b in S.waste_batch_events],[2,1])

    def test_full_batches_use_minimum_ports_without_overlap_or_rotation(self):
        for w,h,expected_ports in [(4,4,1),(4,6,2),(2,5,1),(2,6,1)]:
            with self.subTest(shape=(w,h)):
                S=Scene();S.manual_aspirations=[];items=[];original=Scene._record
                for r in range(1,17):
                    lane=ops.Lane(1,r)
                    items.append((lane,S.add(Drop(f'WG{r}',(44-w,lane.Y0,w,h),w*h*.05))))
                S.step(changed=[])
                def trace(scene,*args,**kw):
                    original(scene,*args,**kw)
                    cells=[d.target_cells() for d in scene.drops.values() if d.vol>0]
                    self.assertEqual(len(frozenset().union(*cells)),sum(map(len,cells)))
                    for d in scene.drops.values():
                        if getattr(d,'waste_grid_packet',False):
                            self.assertEqual(contour.bounds(d.target_cells())[2:],(w,h))
                with patch.object(Scene,'_record',trace),patch.object(rigid,'reshape',side_effect=AssertionError('废液不得旋转整形')):
                    waste_grid.send(S,items,46,{k:None for k in waste_grid.PORTS})
                info=S.manual_aspirations[-1][1]
                self.assertEqual(len(info['port_volumes']),expected_ports)
                self.assertTrue(all(e['row_ready'] for e in S.waste_grid_events))
                self.assertAlmostEqual(info['volume_ul'],16*w*h*.05)

    def test_r04_three_batches_keep_completed_docks_until_one_aspiration(self):
        S=Scene();S.manual_aspirations=[];lanes=[];completed={};previous_relay=[None]
        original=Scene._record
        for row in range(1,17):
            lane=ops.Lane(1,row);lanes.append(lane)
            S.add(Drop(lane.name,(lane.X0+9,lane.Y0,25,4),5.0))
        S.step(changed=[])
        def trace(scene,*args,**kw):
            original(scene,*args,**kw)
            powered=frozenset().union(*(d.target_cells() for d in scene.drops.values() if d.rects()))
            for name,cells in list(completed.items()):
                if name not in scene.drops:
                    self.assertTrue(any(r is previous_relay[0] for r,info in scene.manual_aspirations),
                                    '本批全部就绪/手工吸液边界前取消了收集电极')
                    del completed[name]
                else:
                    self.assertEqual(scene.get(name).target_cells(),cells)
                    self.assertTrue(cells<=powered)
            for d in scene.drops.values():
                if d.name.startswith('BLOB') and waste_grid.area(d)==256:
                    completed[d.name]=d.target_cells()
                if getattr(d,'waste_grid_packet',False):
                    self.assertIn(contour.bounds(d.target_cells())[2:],((4,4),(3,4)))
                for x,y in d.target_cells():
                    self.assertFalse(23<=y<172 and any(start<=x<start+18 for start in (50,90,130,170,210)))
            previous_relay[0]=scene.relays[-1]
        def unchanged(scene,drop,rect):
            self.assertEqual(drop.rect,rect,'已是25×4母框，不允许额外整形')
        with patch.object(Scene,'_record',trace),patch.object(rigid,'reshape',side_effect=unchanged):
            waste_grid.supernatant_bites(S,lanes,{k:None for k in waste_grid.PORTS},0,collect_three=True)
        infos=[info for relay,info in S.manual_aspirations]
        self.assertEqual([i['hole'] for i in infos],['F3/F2/F1','F3/F2/F1','F1'])
        self.assertEqual([i['volume_ul'] for i in infos],[35.2,32.0,12.8])
        self.assertEqual(infos[0]['port_volumes'],{'F3':12.8,'F2':12.8,'F1':9.6})
        self.assertEqual(infos[1]['port_volumes'],{'F3':12.8,'F2':9.6,'F1':9.6})
        self.assertTrue(all(e['scheduled_drops']==16 for e in S.waste_parallel_events))
        self.assertTrue(all(e['entries'][4]['frame']<e['row_merges'][0] for e in S.waste_parallel_events))
        self.assertEqual(len(S.waste_grid_events),112)
        self.assertEqual([e['retained_port'] for e in S.waste_batch_events],[3,2,1,3,2,1,1])
        self.assertAlmostEqual(sum(i['volume_ul'] for i in infos),80)
        self.assertTrue(all(d.vol==0 and d.magnet_only for d in S.drops.values()))

    def test_r04_grouping_rejects_other_liquid_volumes(self):
        S=Scene();S.manual_aspirations=[];items=[]
        for row in range(1,17):
            lane=ops.Lane(1,row)
            items.append((lane,S.add(Drop(lane.name,(lane.X0,lane.Y0,20,6),6.0))))
        with self.assertRaisesRegex(AssertionError,'单列R04'):
            waste_grid.bites(S,items,{},collect_three=True)

    def test_a05_three_ports_collected_before_one_aspiration(self):
        S=Scene();S.manual_aspirations=[];items=[]
        for row in range(1,17):
            lane=ops.Lane(1,row);items.append((lane,S.add(Drop(f'A{row}',(34,lane.Y0,6,5),1.5))))
        S.step(changed=[])
        waste_grid.egress_pieces(S,items,{},collect_a05=True)
        self.assertEqual(len(S.manual_aspirations),1)
        info=S.manual_aspirations[0][1]
        self.assertEqual(info['port_volumes'],{'F3':8.0,'F2':8.0,'F1':8.0})
        self.assertEqual([r[2:] for r in info['rects']],[[16,10]]*3)

if __name__=='__main__':unittest.main()
