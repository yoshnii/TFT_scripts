"""Only native PCR-block construction, timing, and adjacent electrode masks."""
import json
import unittest
import xml.etree.ElementTree as E
import geom
import protocol
from model import Cmd, CommandBlock, Flow, Script
from scene import Drop
from update_pcr_block_local import temperature_trace


class PcrBlockTests(unittest.TestCase):
    def seeded(self):
        p=protocol.Protocol((1,))
        for lane in p.ALL:
            p.S.add(Drop(lane.name,(lane.X0,lane.Y0,10,5),2.5))
        p.S.step();p.S.flush()
        return p

    def test_native_block_is_six_temperature_commands_not_the_outer_section(self):
        p=self.seeded();f=p.pcr_thermal_block('A','A08 PCR')
        script=Script('test',[f],'ChipArea_192_256_96_SIRO',1000)
        raw=E.fromstring(script.xml())[0]
        self.assertEqual(raw.get('Repeats'),'1')
        block=json.loads(raw.get('UnBrokenBlockList'))[0]
        self.assertEqual(block['Cycle'],30)
        self.assertTrue(block['Enable'])
        self.assertEqual(len(block['IDList']),6)
        in_block=[c for c in raw if int(c.get('ID')) in block['IDList']]
        self.assertEqual([c.get('Params') for c in in_block if c.get('Component')=='Heater1'],
                         ['95,1','60,1','72,1'])
        self.assertEqual([c.get('Timeout') for c in in_block if c.get('Component')=='Delay'],['30','60','45'])
        self.assertFalse(any(c.get('Component')=='HighVoltage' for c in in_block))
        trace=temperature_trace(raw)
        self.assertEqual(len(trace),186)
        temperatures=[c.get('Params') for c in trace if c.get('Component')=='Heater1']
        self.assertEqual(temperatures,['95,1']+['95,1','60,1','72,1']*30+['72,1','4,1'])
        delays=[int(c.get('Timeout')) for c in trace if c.get('Component')=='Delay']
        self.assertEqual(delays,[180]+[30,60,45]*30+[60,30])
        self.assertEqual(sum(delays),4320)

    def test_two_frame_pulse_spans_from_preheat_to_final_4C_and_never_moves(self):
        p=self.seeded();f=p.pcr_thermal_block('A','A08 PCR')
        pulse=f.cmds[-1]
        self.assertEqual(pulse.params,'SCRIPTCYCLE_TRUE')
        self.assertEqual([(r.dur,r.amp) for r in pulse.relays],[(1000,0),(1000,50)])
        self.assertEqual(pulse.relays[0].codes,pulse.relays[1].codes)
        self.assertEqual(len(pulse.relays[0].codes),16)
        first=next(c for c in f.cmds if c.comp=='Heater1')
        self.assertEqual(pulse.pre,(first,'SS'))
        final_delay=[c for c in f.cmds if c.comp=='Delay'][-1]
        self.assertEqual(final_delay.timeout,30)
        self.assertEqual(f.sync,[final_delay,pulse])
        self.assertNotIn(pulse,f.blocks[0].cmds)
        self.assertFalse(p._restore_after_pulse)

    def test_local_cycle_does_not_repeat_preheat_or_final_extension(self):
        p=self.seeded();f=p.pcr_thermal_block('A','A08 PCR')
        position=next(c for c in f.cmds if c.comp=='HighVoltage' and c.params=='SCRIPTCYCLE_FALSE')
        self.assertEqual(f.blocks[0].timeout,315)
        self.assertEqual(f.timeout,position.timeout+240+315*30+210)
        controls=[c for c in f.cmds if c.comp in ('Heater1','Delay')]
        self.assertEqual(len(controls),12)
        self.assertEqual(controls[-4].pre,(f.blocks[0].cmds[-1],'FS'))
        self.assertEqual(controls[-2].pre,(controls[-3],'FS'))

    def test_ordinary_flow_serialization_and_parallel_timing_are_unchanged(self):
        start=Cmd('a','','',3);loop=Cmd('b','','',2);timer=Cmd('c','','',180)
        loop.pre=(start,'FS');timer.pre=(loop,'SS')
        f=Flow('regular',[start,loop,timer],chain_commands=False)
        self.assertEqual(f.timeout,183)
        self.assertNotIn('UnBrokenBlockList',Script('test',[f],'test',1000).xml())

    def test_new_section_schema_accepts_multiple_parallel_heaters(self):
        p=protocol.Protocol((1,2))
        for lane in p.ALL:
            p.S.add(Drop(lane.name,(lane.X0+4,lane.Y0,10,5),2.5))
        p.S.step();p.S.flush()
        f=p.pcr_thermal_block('A','A08 PCR')
        self.assertEqual(len(f.blocks[0].cmds),12)
        self.assertEqual(f.blocks[0].timeout,315)
        self.assertEqual(len(f.sync),3)


if __name__=='__main__':unittest.main()
