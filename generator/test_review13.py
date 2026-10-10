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

    def test_b02_single_port_one_aliquot_per_reaction(self):
        S=Scene();S.manual_aspirations=[];loads=[]
        for ln in ops.lanes_all((1,)):S.add(Drop(ln.name,(30,ln.Y0,7,4),1.3))
        S.step()
        rigid.deliver_drops(S,'G8',.7,(1,),'TG8',controlled=True,on_load=loads.append)
        self.assertEqual(loads[0]['port_volumes'],{'E1':11.2})
        collected=[e for e in S.pipeline_events if e['kind']=='reagent_collect']
        self.assertEqual(len(collected),16)
        self.assertTrue(all(e['incoming_ul']==.7 for e in collected))
        for ln in ops.lanes_all((1,)):
            self.assertEqual(S.get(ln.name).vol,2)
            self.assertEqual(len(S.get(ln.name).target_cells()),40)

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
