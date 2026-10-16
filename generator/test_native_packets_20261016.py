"""审核后新增的等分/原形子滴/唯一section编号回归，仅离线。"""
import unittest
from unittest.mock import patch
import geom,ops,protocol
from scene import Scene,Drop
from model import Flow,Cmd
from project_structure import number_sections
from native_packets import partition,_configuration
from native_waste import schedule


class NativePacketTests(unittest.TestCase):
    def test_reagent_mass_and_leaf_channel_sizes(self):
        expected={'G4':.4,'AMP':2.,'G14':2.5,'G8':.7,'LIG':1.4,'EDTA':.2,'G16':1.7}
        for reagent,volume in expected.items():
            ports,n,pv,w,h,per_lane=_configuration(reagent)
            self.assertAlmostEqual(ports*n*pv,16*volume)
            self.assertAlmostEqual(per_lane*pv,volume)
            self.assertLessEqual(w,4);self.assertLessEqual(h,6)

    def test_five_equal_children_open_one_grid_at_a_time(self):
        s=Scene();d=s.add(Drop('G14',(40,8,30,8),10.))
        d.electrode_area=240;d.margin_budget=40;d.native_authorized_padding=True;s.step()
        children=partition(s,[d],5,'x',3)[0]
        self.assertEqual([q.vol for q in children],[2.]*5)
        self.assertEqual(sum(len(q.target_cells()) for q in children),240)
        self.assertEqual(sum(q.vol for q in children),10.)
        e=s.split_events[0]
        self.assertEqual(e['gaps'][-1],3)
        for before,after in zip(e['positions'],e['positions'][1:]):
            self.assertTrue(all(abs(a[0]-b[0])+abs(a[1]-b[1])<=1 for a,b in zip(before,after)))

    def test_cdna_quarter_two_equal_cuts_then_outlet_waste(self):
        p=protocol.Protocol((1,))
        for ln in p.ALL:p.S.add(Drop(ln.name,geom.lane_target(1,ln.r,2.,'left'),2.))
        p.S.step();ops.split_all(p.S,p.ALL,.5,'A')
        for ln in p.ALL:
            self.assertEqual(p.S.get(ln.name).vol,.5)
            self.assertEqual(p.S.get(ln.name).rect,(ln.X0+1,ln.Y0,3,4))
            away=p.S.get('A'+ln.id)
            self.assertEqual(away.vol,1.5)
            self.assertEqual(away.rect,(ln.X0+24,ln.Y0,8,4))
        self.assertEqual(len(p.S.split_events),32)
        self.assertTrue(all(e['volumes'][0]==e['volumes'][1] for e in p.S.split_events))

    def test_c04_actual_volume_not_six_microlitres(self):
        self.assertAlmostEqual(8*.7375,5.9)
        self.assertEqual(8*3*5,120)
        self.assertNotEqual(8*.7375,120*.05)
        p=schedule(8,3,5,'C04')
        self.assertEqual([i for i,x in enumerate(p) if x['flush']],[3,7])
        self.assertTrue(all(x['specs'][k]['W']==15 for x in p for k in x['specs']))

    def test_renumber_changes_names_not_command_or_flow_ids(self):
        flows=[]
        for i,name in enumerate(('A01 load','A02 receive','A02 dispense','A03 mix')):
            c=Cmd('motion','HighVoltage','CMD_SCRIPTCYCLE',1);c.id=1001+i
            f=Flow(name,[c]);f.id=1000+i;flows.append(f)
        class Script:name='PTseq_16_A'
        script=Script();script.flows=flows
        rows=[dict(script=script.name,section_id=1001,next_script=script.name,next_section_id=1002)]
        number_sections([script],rows)
        self.assertEqual([f.name.split()[0] for f in flows],['A01','A02','A03','A04'])
        self.assertEqual([f.id for f in flows],[1000,1001,1002,1003])
        self.assertEqual([f.cmds[0].id for f in flows],[1001,1002,1003,1004])
        self.assertEqual(rows[0]['next_section_name'],'A03 dispense')

    def test_unrelated_normalization_api_not_redirected_to_main_flow(self):
        import native_waste,waste_grid
        s=Scene();ln=ops.Lane(1,1);d=s.add(Drop('old',(20,28,2,4),.4))
        with patch.object(waste_grid,'egress_pieces',return_value='legacy') as legacy:
            self.assertEqual(native_waste.egress_pieces(s,[(ln,d)],{}),'legacy')
            legacy.assert_called_once()

    def test_explicit_single_stop_failure_precedes_scene_mutation(self):
        import native_waste
        s=Scene();items=[]
        for ln in ops.lanes_all((1,)):
            items.append((ln,s.add(Drop(ln.name,geom.lane_target(1,ln.r,5.,'right'),5.))))
        before=[d.rect for ln,d in items]
        with self.assertRaisesRegex(AssertionError,'一次装入'):
            native_waste.bites(s,items,{},single_stop=True)
        self.assertEqual(s.nsteps,0);self.assertEqual(before,[d.rect for ln,d in items])


if __name__=='__main__':unittest.main()
