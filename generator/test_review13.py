"""用户逐项复核的离线回归；不运行GUI或芯片。"""
import unittest
from unittest.mock import patch
import contour,geom,ops,rigid,protocol
import manual_sections
from model import Cmd,Flow
from scene import Relay
from scene import Scene,Drop

class Review13Tests(unittest.TestCase):
    def test_full_mix_sweeps_both_edges_without_changing_shape(self):
        S=Scene();lane=ops.Lane(1,1);d=S.add(Drop(lane.name,(20,lane.Y0,10,4),2));S.step()
        seen=[];original=Scene._record
        def trace(scene,*a,**kw):
            original(scene,*a,**kw);seen.append(d.rect)
        with patch.object(Scene,'_record',trace):ops.mix_all(S,[lane],2,full_region=True)
        self.assertEqual(min(r[0] for r in seen),lane.X0)
        self.assertEqual(max(r[0]+r[2] for r in seen),lane.X0+geom.LANE_L)
        self.assertEqual(set(r[2:] for r in seen),{(10,4)})

    def test_b01_compact_receiving_keeps_26_cells(self):
        S=Scene();lanes=ops.lanes_all((1,))
        ops.load_holes(S,lanes,'S',1.30,'L',manual=True)
        for ln in lanes:
            d=S.get(ln.name);self.assertEqual(d.rect[2:],(7,4));self.assertEqual(len(d.target_cells()),26)

    def test_only_named_reagents_can_use_controlled_delivery(self):
        S=Scene()
        with self.assertRaisesRegex(AssertionError,'B02/B10'):
            for lane in ops.lanes_all((1,)):S.add(Drop(lane.name,(30,lane.Y0,10,4),2))
            rigid.deliver_drops(S,'LIG',1.4,(1,),'TLIG',controlled=True)

    def test_b02_single_port_two_rectangles_per_reaction(self):
        """B02：E1单孔11.2µL；每道2×4(0.40µL)+2×3(0.30µL)两个完整矩形，运输与进入反应区不变形。"""
        S=Scene();S.manual_aspirations=[];loads=[];original=Scene._record;shapes=set()
        for ln in ops.lanes_all((1,)):S.add(Drop(ln.name,(30,ln.Y0,7,4),1.3))
        S.step()
        def trace(scene,*args,**kw):
            original(scene,*args,**kw)
            for d in scene.drops.values():
                if d.name.startswith('TG8'):
                    cells=d.target_cells();b=contour.bounds(cells)
                    self.assertEqual(len(cells),b[2]*b[3],'B02小滴须为完整矩形')
                    shapes.add(b[2:])
        with patch.object(Scene,'_record',trace):
            rigid.deliver_drops(S,'G8',.7,(1,),'TG8',controlled=True,on_load=loads.append)
        self.assertEqual(loads[0]['port_volumes'],{'E1':11.2})
        self.assertEqual(shapes,{(2,4),(2,3)})
        collected=[e for e in S.pipeline_events if e['kind']=='reagent_collect']
        self.assertEqual(sorted(e['incoming_ul'] for e in collected),[.3]*16+[.4]*16)
        merge=S.reaction_merge_events[-1]
        self.assertEqual(sorted(merge['rows']),list(range(1,17)))
        for ln in ops.lanes_all((1,)):
            d=S.get(ln.name)
            self.assertEqual(d.vol,2)
            self.assertEqual(d.rect[2:],(8,5))
            self.assertEqual(len(d.target_cells()),40)

    def test_unused_wells_and_inter_lane_rows_are_rejected(self):
        S=Scene();S.add(Drop('X',(6,6,2,4),.4))
        with self.assertRaisesRegex(AssertionError,'未使用孔'):S.step()
        S=Scene();S.add(Drop('X',(geom.SAFE_X,geom.SAFE_Y,2,4),.4));S.step()   # 左上角大孔恰留4格
        S=Scene();ln=ops.Lane(1,8);S.add(Drop('Y',(14,ln.Y0-1,2,7),.7))
        with self.assertRaisesRegex(AssertionError,'两行之间'):S.step()

    def test_c01_compact_guest_merges_directly_without_strip(self):
        """C01：34格磁珠液保持整高紧凑块，与14×6产物整高贴合后直接成为20×6缺2格；不拉成17×2细条。"""
        S=Scene();lanes=ops.lanes_all((1,));original=Scene._record;seen=[]
        for ln in lanes:
            S.add(Drop(ln.name,(ln.X0+20,ln.Y0,14,6),4.2))
            g=S.add(Drop(f'TG16{ln.id}',(ln.X0+10,ln.Y0,6,6),1.7))
        S.step()
        def trace(scene,*args,**kw):
            original(scene,*args,**kw)
            for d in scene.drops.values():
                cells=d.target_cells();b=contour.bounds(cells)
                self.assertGreaterEqual(b[3],5,'不得出现细长条')
                self.assertTrue(contour.orthoconvex(cells),'不得出现L形或凹口')
                seen.append(len(cells))
        with patch.object(Scene,'_record',trace):
            ops.merge_all(S,lanes,'TG16')
        for ln in lanes:
            d=S.get(ln.name)
            self.assertEqual(d.rect[2:],(20,6));self.assertEqual(d.target_cells(),contour.fill(d.rect,118))

    def test_b10_single_port_without_changing_dose(self):
        S=Scene();S.manual_aspirations=[];loads=[]
        for ln in ops.lanes_all((1,)):S.add(Drop(ln.name,(30,ln.Y0,16,5),4.0))
        S.step()
        rigid.deliver_drops(S,'EDTA',.2,(1,),'TEDTA',controlled=True,on_load=loads.append)
        self.assertEqual(loads[0]['port_volumes'],{'E1':3.2})
        self.assertTrue(all(S.get(ln.name).vol==4.2 for ln in ops.lanes_all((1,))))

    def test_empty_model_frame_not_exported_as_a_command(self):
        P=protocol.Protocol((1,));P.S.step()
        self.assertIsNone(P.cmd('不导出空步骤'))

    def test_post_aspiration_hold_goes_to_next_command_not_next_flow(self):
        held=Relay(['(27,29)(4,3)'],1000,55)
        asp=Cmd('吸液','HighVoltage','CMD_SCRIPTCYCLE',2,'SCRIPTCYCLE_FALSE',
                [Relay(['(27,29)(4,3)','(173,46)(16,16)'],1000,55),held])
        asp.id=10;asp.aspiration_gates=[(0,dict(hole='F1',volume_ul=12.8))]
        move=Cmd('实际移动','HighVoltage','CMD_SCRIPTCYCLE',1,'SCRIPTCYCLE_FALSE',
                 [Relay(['(27,28)(4,3)'],750,55)])
        move.id=11
        flow=Flow('B05',[asp,move]);flow.id=12
        class Script:flows=[flow]
        manual_sections.split_aspiration_commands([Script()])
        self.assertEqual(len(flow.cmds),2)
        self.assertEqual(move.relays[0].codes,held.codes)
        self.assertFalse(hasattr(flow,'carry_relays'))

if __name__=='__main__':unittest.main()
