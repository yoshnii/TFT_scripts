"""All-frame address compensation, with physical-mapping uncertainty explicit."""
import csv,hashlib,io,json,subprocess,tempfile,unittest
from pathlib import Path
import xml.etree.ElementTree as E

from electrode_address_compensation import (CONFIG,REPORT,address_rotation,
    layout_coordinate_script,publication_files,transform_script,verify)
from rotate_project_180 import BASE_CHIP,ROTATED_CHIP

ROOT=Path(__file__).resolve().parent.parent
PROJECT=ROOT/'projects/PTseq_SIRO16_A_10x'
BASE='9a5e4645b8d45557a0f2c98e074abc8c5634f8a2'


def baseline(name,ref=BASE):
    return subprocess.check_output(['git','show',ref+':projects/PTseq_SIRO16_A_10x/'+name],cwd=ROOT)


class AddressCompensationTests(unittest.TestCase):
    def test_every_frame_is_compensated_and_nothing_else_in_xml_changes(self):
        frames=rectangles=0
        for name in ('main.elab','PTseq_16_A.elab'):
            old=baseline(name).decode();new=(PROJECT/name).read_text()
            self.assertEqual(new,transform_script(old))
            audit=verify(old,new);frames+=audit['frames'];rectangles+=audit['rectangles']
            self.assertEqual(layout_coordinate_script(PROJECT,name),old)
        self.assertEqual((frames,rectangles),(18951,630498))

    def test_orange_layout_manifest_and_flowchart_are_byte_identical(self):
        for name in (BASE_CHIP+'.csv',ROTATED_CHIP+'.csv','PTseq_SIRO16_A_10x.elabprj',
                     'main.elab.layout','PTseq_16_A.elab.layout'):
            self.assertEqual((PROJECT/name).read_bytes(),baseline(name))
        self.assertEqual(E.parse(PROJECT/'PTseq_16_A.elab').getroot().get('ChipsetType'),ROTATED_CHIP)

    def test_all_address_masks_match_original_blue_numbers_but_not_its_layout_model(self):
        blue=E.fromstring(baseline('PTseq_16_A.elab','tft-first-on-chip-water-run-20261022'))
        actual=E.parse(PROJECT/'PTseq_16_A.elab').getroot()
        self.assertEqual([r.attrib for r in blue.iter('RelayItem')],[r.attrib for r in actual.iter('RelayItem')])
        self.assertEqual(actual[0][0][0].get('RelayCode').split(';')[0],'(28,19)(4,8)')
        self.assertNotEqual(blue.get('ChipsetType'),actual.get('ChipsetType'))

    def test_operator_addresses_and_warning_match_this_mode(self):
        original=list(csv.reader(io.StringIO(baseline('手工吸液顺序.csv','tft-first-on-chip-water-run-20261022').decode('utf-8-sig'))))
        with (PROJECT/'手工吸液顺序.csv').open(encoding='utf-8-sig',newline='') as f:actual=list(csv.reader(f))
        self.assertEqual(original,actual)
        self.assertIn('本版基于设备寻址反转180°的假设',(PROJECT/'手工加液运行.html').read_text())

    def test_repeated_publishing_does_not_apply_a_second_compensation(self):
        self.assertEqual(address_rotation(PROJECT),180)
        files,proof=publication_files(PROJECT)
        self.assertEqual(files,{})
        self.assertFalse(proof['physical_device_transform_verified'])
        for row in proof['scripts']:
            self.assertEqual(row['after_sha256'],hashlib.sha256((PROJECT/row['file']).read_bytes()).hexdigest())

    def test_fresh_generator_output_can_retain_existing_compensation(self):
        with tempfile.TemporaryDirectory(prefix='tft-address-compensation-') as tmp:
            folder=Path(tmp)
            for name in ('PTseq_SIRO16_A_10x.elabprj','main.elab','PTseq_16_A.elab',ROTATED_CHIP+'.csv'):
                (folder/name).write_bytes(baseline(name))
            (folder/CONFIG).write_bytes((PROJECT/CONFIG).read_bytes())
            files,proof=publication_files(folder,fresh_layout_output=True)
            self.assertEqual(files[folder/'PTseq_16_A.elab'],(PROJECT/'PTseq_16_A.elab').read_bytes())
            self.assertEqual(proof['scope'],'All electrode addresses only; retain orange chip layout')


if __name__=='__main__':unittest.main()
