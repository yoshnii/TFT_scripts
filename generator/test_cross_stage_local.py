"""Release boundaries and physical invariants for the ce3767 cross-stage update."""
import csv,hashlib,io,json,subprocess,unittest
from pathlib import Path
import xml.etree.ElementTree as E
from update_cross_stage_local import BASE,REPORT,TARGET,WASTE,CASES
from update_transport_cleanup_local import last_hv
from update_fixed_waste_local import relay_key
from waste_recovery import boxes

ROOT=Path(__file__).resolve().parent.parent
PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'
RECEIVING={c[3] for c in CASES}

def source(name):
    return subprocess.check_output(['git','show',BASE+':projects/PTseq_SIRO16_A_10x/'+name],cwd=ROOT)

class CrossStageReleaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old={int(f.get('ID')):f for f in E.fromstring(source('PTseq_16_A.elab'))}
        cls.new={int(f.get('ID')):f for f in E.parse(PROJECT/'PTseq_16_A.elab').getroot()}
        cls.proof=json.loads((PROJECT/REPORT).read_text())

    def test_only_twenty_scheduled_flows_change(self):
        self.assertEqual(list(self.old),list(self.new))
        self.assertEqual({i for i in self.old if E.tostring(self.old[i])!=E.tostring(self.new[i])},TARGET)
        for name in ('PTseq_SIRO16_A_10x.elabprj','ChipArea_192_256_96_SIRO.csv','手工加液顺序.csv'):
            self.assertEqual((PROJECT/name).read_bytes(),source(name))
        for i in (4013,51002,50004):
            self.assertEqual(E.tostring(self.old[i]),E.tostring(self.new[i]))

    def test_graph_and_all_non_electrode_actions_unchanged(self):
        for i in TARGET:
            a,b=self.old[i],self.new[i]
            self.assertEqual({k:v for k,v in a.attrib.items() if k!='Timeout'},
                             {k:v for k,v in b.attrib.items() if k!='Timeout'})
            self.assertEqual(len(a),len(b))
            for k,(x,y) in enumerate(zip(a,b)):
                excluded={'Timeout','Name'} if i in RECEIVING and k==0 else {'Timeout'}
                self.assertEqual({q:v for q,v in x.attrib.items() if q not in excluded},
                                 {q:v for q,v in y.attrib.items() if q not in excluded})
                if x.get('Component')!='HighVoltage':self.assertEqual(E.tostring(x),E.tostring(y))

    def test_manual_boundary_is_still_one_static_frame(self):
        for i in RECEIVING:
            self.assertEqual(len(self.new[i][-1]),1)
            a,b=self.old[i][-1],self.new[i][-1]
            self.assertEqual(a.attrib,b.attrib)
            self.assertEqual({k:v for k,v in a[0].attrib.items() if k!='RelayCode'},
                             {k:v for k,v in b[0].attrib.items() if k!='RelayCode'})
            self.assertEqual([r for r in boxes(a[0].get('RelayCode')) if r[1]<27],
                             [r for r in boxes(b[0].get('RelayCode')) if r[1]<27])
            self.assertEqual(self.old[i][0][0].get('RelayCode'),self.new[i][0][0].get('RelayCode'))
        self.assertEqual(E.tostring(self.old[1019][0]),E.tostring(self.new[1019][0]))

    def test_layout_edits_only_seven_display_names(self):
        a=E.fromstring(source('PTseq_16_A.elab.layout'))
        b=E.parse(PROJECT/'PTseq_16_A.elab.layout').getroot()
        allowed={self.new[i][0].get('ID') for i in RECEIVING};changed=set()
        for x,y in zip(a.iter(),b.iter()):
            if x.attrib!=y.attrib:
                self.assertIn(x.get('ID'),allowed);changed.add(x.get('ID'))
                y.set('Name',x.get('Name'))
        self.assertEqual(changed,allowed)
        self.assertEqual(E.tostring(a),E.tostring(b))

    def test_every_row_gets_exact_reagent_before_one_synchronous_fusion(self):
        for c in self.proof['top_cases']:
            self.assertGreater(c['contact_frame'],c['all_ready_frame'])
            self.assertEqual(c['geometry']['aspirated_ul'],0)
            self.assertTrue(c['geometry']['static_geometry_passed'])
            for row in range(1,17):
                self.assertAlmostEqual(sum(e['actual_ul'] for e in c['leaves'] if e['row']==row),c['actual_added_ul_per_lane'])
            self.assertGreater(c['overlap']['host_and_reagent_motion_frames'],0)
            self.assertGreater(c['overlap']['delivery_before_all_cuts_finished_frames'],0)
            i=c['ids'][-1]
            self.assertEqual(relay_key(last_hv(self.old[i])),relay_key(last_hv(self.new[i])))

    def test_every_cut_is_equal_horizontal_and_symmetrically_opens_one_cell(self):
        for c in self.proof['top_cases']+self.proof['waste_cases']:
            self.assertTrue(c['cut_dwell']['per_child_gap_and_final_holds_passed'])
            for e in c['splits']:
                self.assertEqual((e['axis'],e['arity']),('x',2))
                self.assertAlmostEqual(e['volumes'][0],e['volumes'][1])
                self.assertTrue(e['partition_preserves_mother_cells'])
                for (a,b),(x,y) in zip(e['positions'],e['positions'][1:]):
                    self.assertEqual((a[0]-x[0],y[0]-b[0]),(1,1))
                    self.assertEqual(a[1:],x[1:]);self.assertEqual(b[1:],y[1:])
            traces=c['hold_trace'] if 'hold_trace' in c else c['fixed']['hold_trace']
            self.assertTrue(all(p['all_reshape_holds_at_least_4000ms'] for p in traces))

    def test_captured_maximum_gaps_and_holds_not_lost_to_parallelism(self):
        by_case={c['case']:c for c in self.proof['waste_cases']}
        r=by_case['R captured']['gap_events'];c=by_case['C captured']['gap_events']
        self.assertEqual([g['gap'] for g in r],[24,16,16,24,28,26,36])
        self.assertEqual([g['gap'] for g in c],[12,14,14,22,28,26,36])
        for g in r+c:
            if g['first']:continue
            self.assertEqual(g['frame_ms'][:3],[1000,3000,3000])
            self.assertEqual(g['frame_ms'][-1],5000)

    def test_receiver_volume_full_coverage_and_original_suction(self):
        packets=receivers=0;mass=0.
        for c in self.proof['waste_cases']:
            f=c['fixed'];packets+=f['admitted_packets'];receivers+=f['receiver_count']
            mass+=sum(e['packet_ul'] for e in f['admission_events'])
            for key in ('mass_conserved_each_scene_frame','receiver_footprints_unchanged',
                        'full_coverage_and_no_duplicate_codes','no_intermediate_collection_blocks'):
                self.assertTrue(f[key])
            self.assertTrue(all(e['actual_ul']==0 for e in f['preopen_events']))
        self.assertEqual((packets,receivers),(496,31));self.assertAlmostEqual(mass,356.16)
        for i in WASTE:
            self.assertEqual([relay_key(r) for r in list(self.old[i][-1])[-24:]],
                             [relay_key(r) for r in list(self.new[i][-1])[-24:]])
        for x,y in ((2013,51000),(4013,51002)):
            self.assertEqual(last_hv(self.new[x]).get('RelayCode'),self.new[y][0][0].get('RelayCode'))

    def test_dual_bottom_routes_only_for_retained_candidates(self):
        for c in self.proof['waste_cases']:
            moving=c['overlap']['two_bottom_corridors_occupied_frames']
            self.assertEqual(moving>0,not c['unchanged'])
            for e in c['arrivals']:
                self.assertIn(e['lane_y'],(172,182))
                if e['port']==1 or c['unchanged']:self.assertEqual(e['lane_y'],172)

    def test_only_aspiration_frame_numbers_change_in_operator_csv(self):
        a=list(csv.DictReader(io.StringIO(source('手工吸液顺序.csv').decode('utf-8-sig'))))
        with (PROJECT/'手工吸液顺序.csv').open(encoding='utf-8-sig') as fh:b=list(csv.DictReader(fh))
        self.assertEqual(len(a),len(b))
        for x,y in zip(a,b):
            i=int(x['吸液流程组编号'])
            if i in WASTE:
                self.assertEqual(int(y['末尾帧号']),len(self.new[i][-1]))
                x.pop('末尾帧号');y.pop('末尾帧号')
            self.assertEqual(x,y)

    def test_timeout_totals_hash_and_workbook_current(self):
        p=self.proof
        saved=sum(int(self.old[i].get('Timeout'))-int(self.new[i].get('Timeout')) for i in TARGET)
        self.assertEqual((saved,p['total_saved_seconds']),(1154,1154))
        for i in TARGET:
            self.assertEqual(int(self.new[i].get('Timeout')),sum(int(r.get('Duration')) for r in self.new[i].iter('RelayItem'))//1000)
        self.assertEqual(int(E.parse(PROJECT/'main.elab').getroot()[0].get('Timeout')),
                         sum(int(f.get('Timeout'))*int(f.get('Repeats','1')) for f in self.new.values()))
        self.assertEqual(p['script_sha256'],hashlib.sha256((PROJECT/'PTseq_16_A.elab').read_bytes()).hexdigest())
        workbook=ROOT.parent/'片上测试1/TFT片上试剂体积表_单列.xlsx'
        self.assertEqual(p['workbook_sha256'],hashlib.sha256(workbook.read_bytes()).hexdigest())

if __name__=='__main__':unittest.main()
