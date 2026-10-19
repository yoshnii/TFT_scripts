"""Exact native bottom masks for the seven approved waste sections."""
import argparse
import csv
from pathlib import Path
import xml.etree.ElementTree as E
from PIL import Image,ImageDraw,ImageFont
from waste_recovery import boxes
from update_waste_timing_local import TARGETS


def render(folder,out):
    fs=[f for f in E.parse(folder/'PTseq_16_A.elab').getroot() if f.get('Name').split()[0] in TARGETS]
    features=list(csv.DictReader((folder/'ChipArea_192_256_96_SIRO.csv').open(encoding='utf-8-sig')))
    font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',15)
    tw,th,scale=800,125,3
    img=Image.new('RGB',(2*tw,40+2*th*len(fs)),'white');draw=ImageDraw.Draw(img)
    draw.text((10,8),'Exact native waste masks: transport / later transport / full pool / final suction mask',fill='black',font=font)
    for row,f in enumerate(fs):
        frames=list(f[-1]);bars=[i for i,r in enumerate(frames[:-23]) if any(rc[2:]==(8,16) for rc in boxes(r.get('RelayCode')))]
        assert bars
        picks=[bars[0],bars[len(bars)//2],len(frames)-23,len(frames)-1]
        for j,k in enumerate(picks):
            ox=(j%2)*tw+12;oy=40+(2*row+j//2)*th+25
            def box(r):
                x,y,w,h=r;return ox+x*scale,oy+(y-168)*scale,ox+(x+w)*scale,oy+(y+h-168)*scale
            draw.text((ox,oy-24),f'{f.get("Name").split()[0]} / command {f[-1].get("ID")} / row {k+1} / {frames[k].get("Duration")} ms',fill='black',font=font)
            for x in range(0,257,4):draw.line((ox+x*scale,oy,ox+x*scale,oy+24*scale),fill='#e4e4e4')
            for y in range(168,193,2):draw.line((ox,oy+(y-168)*scale,ox+256*scale,oy+(y-168)*scale),fill='#e4e4e4')
            for r in boxes(frames[k].get('RelayCode')):
                if r[1]>=168:draw.rectangle(box(r),fill='#63d655',outline='#16802a')
            draw.line((ox,oy+4*scale,ox+256*scale,oy+4*scale),fill='#804090',width=2)
            draw.line((ox,oy+24*scale,ox+256*scale,oy+24*scale),fill='black',width=2)
            for ftr in features:
                if ftr['Type']=='ReagentWell' and float(ftr['StartY'])>=168:
                    draw.ellipse(box(tuple(float(ftr[q]) for q in ('StartX','StartY','Width','Height'))),outline='#184ba0',width=2)
    img.save(out);print(out)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('out',type=Path);a=p.parse_args();render(a.folder,a.out)
