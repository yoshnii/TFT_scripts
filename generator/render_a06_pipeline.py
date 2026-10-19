"""Contact sheets of exact shipped A06 masks, not wet-droplet simulation."""
import argparse
import csv
from pathlib import Path
import xml.etree.ElementTree as E
from PIL import Image,ImageDraw,ImageFont
from waste_recovery import boxes


def render(folder,out):
    flow=E.parse(folder/'PTseq_16_A.elab').getroot().find("Flow[@ID='1019']")
    frames=list(flow[-1]);features=list(csv.DictReader((folder/'ChipArea_192_256_96_SIRO.csv').open(encoding='utf-8-sig')))
    font=ImageFont.truetype('/System/Library/Fonts/Supplemental/Arial.ttf',15)
    # Every ten native frames plus all terminal frames and all union changes.
    picks=set(range(0,len(frames)-23,10))|set(range(len(frames)-23,len(frames)))
    for k in range(1,len(frames)-23):
        old=[r for r in boxes(frames[k-1].get('RelayCode')) if r[1]>=172]
        now=[r for r in boxes(frames[k].get('RelayCode')) if r[1]>=172]
        if any(r[2:]==(16,16) and r not in old for r in now):picks.update(range(max(0,k-2),k+2))
    indices=sorted(picks)
    for page in range(0,len(indices),16):
        selected=indices[page:page+16];scale=4;tw=800;th=170
        img=Image.new('RGB',(tw*2,40+th*((len(selected)+1)//2)),'white');draw=ImageDraw.Draw(img)
        draw.text((12,8),'A06 exact native masks / X0..190, Y168..194 / green=electrodes, not measured liquid',fill='black',font=font)
        for j,k in enumerate(selected):
            ox=(j%2)*tw+18;oy=40+(j//2)*th+30
            def box(r):
                x,y,w,h=r;return ox+x*scale,oy+(y-168)*scale,ox+(x+w)*scale,oy+(y+h-168)*scale
            draw.text((ox,oy-25),f'command 1021 / row {k+1} / {frames[k].get("Duration")} ms',fill='black',font=font)
            for r in boxes(frames[k].get('RelayCode')):
                if r[1]>=168 and r[0]+r[2]<=190:draw.rectangle(box(r),fill='#63d655',outline='#16802a')
            for x in range(0,191,2):draw.line((ox+x*scale,oy,ox+x*scale,oy+26*scale),fill='#dddddd')
            for y in range(168,195,2):draw.line((ox,oy+(y-168)*scale,ox+190*scale,oy+(y-168)*scale),fill='#dddddd')
            draw.line((ox,oy+4*scale,ox+190*scale,oy+4*scale),fill='#804090',width=2)
            draw.line((ox,oy+24*scale,ox+190*scale,oy+24*scale),fill='black',width=2)
            for f in features:
                if f['Type']=='ReagentWell' and float(f['StartY'])>=168 and float(f['StartX'])<190:
                    r=tuple(float(f[q]) for q in ('StartX','StartY','Width','Height'))
                    draw.ellipse(box(r),outline='#184ba0',width=2)
        target=out/f'A06_bottom_{page//16+1}.png';img.save(target);print(target)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('folder',type=Path);p.add_argument('out',type=Path)
    a=p.parse_args();a.out.mkdir(parents=True,exist_ok=True);render(a.folder,a.out)
