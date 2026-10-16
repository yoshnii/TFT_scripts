"""Approved G8/LIG/G16 mother-first rectangles; final packets never reshape."""
import geom
from native_packets import partition


def _move(S,nodes,positions):
    import rigid as R
    assert len(nodes)==len(positions)
    R.run(S,[(d,R.go(d,[('x',x),('y',y)])) for d,(x,y) in zip(nodes,positions)])


def _shape(S,nodes,width,height,reagent):
    import rigid as R
    events=getattr(S,'approved_mother_shapes',[])
    S.approved_mother_shapes=events
    for d in nodes:
        x,y,w,h=d.rect
        assert width*height>=w*h,'approved mother shapes must not remove carrier area'
        assert R.rect_ok(S,d.name,(x,y,width,height))
        assert not getattr(d,'native_packet',False)
    for d in nodes:
        before=list(d.rect);old_area=len(d.target_cells());x,y,_,_=d.rect
        d.electrode_area=width*height
        d.margin_budget=d.electrode_area-geom.cells(d.vol)
        d.native_authorized_padding=True
        d.rect=(x,y,width,height)
        events.append(dict(reagent=reagent,name=d.name,frame=S.nsteps,
            actual_ul=d.vol,before=before,after=list(d.rect),
            before_cells=old_area,after_cells=d.electrode_area,hold_ms=geom.LONG_MS))
    S.step(geom.LONG_MS,changed=[d.name for d in nodes])


def generate(S,fields,reagent):
    """G8/LIG:20×12→10×12→12×10→6×10→6×5→8×4→4×4.
    G16:20×16→10×16→10×8→5×8→8×5→4×5.
    Top layout uses the approved three-cell minimum where sixteen-high mothers
    must open vertically; all normal final queue spacing remains unchanged.
    """
    assert reagent in ('G8','LIG','G16')
    roots=[f['root'] for f in fields]
    for d in roots:d.compact_mother_clearance=True
    _move(S,roots,[(26+128*i,4) for i in range(len(fields))])
    groups=partition(S,roots,2,'x',4)
    nodes=[d for ds in groups for d in ds]
    _move(S,nodes,[(20+128*i+40*j,7 if reagent=='G16' else 4)
                  for i in range(len(fields)) for j in range(2)])
    if reagent in ('G8','LIG'):
        _shape(S,nodes,12,10,reagent)
        nodes=[d for ds in partition(S,nodes,2,'x',4) for d in ds]
        _move(S,nodes,[(d.rect[0],8) for d in nodes])
        # Each six-wide parent yields two compact 6×5 mothers.
        nodes=[d for ds in partition(S,nodes,2,'y',4) for d in ds]
        _move(S,nodes,[(20+128*i+28*j,4+9*k)
                      for i in range(len(fields)) for j in range(4) for k in range(2)])
        _shape(S,nodes,8,4,reagent)
    else:
        nodes=[d for ds in partition(S,nodes,2,'y',3) for d in ds]
        nodes=[d for ds in partition(S,nodes,2,'x',4) for d in ds]
        # Node order is port, initial x half, y half, final x half.
        positions=[]
        for i in range(len(fields)):
            for half in range(2):
                for row in range(2):
                    for col in range(2):positions.append((20+128*i+28*(2*half+col),4+11*row))
        _move(S,nodes,positions)
        _shape(S,nodes,8,5,reagent)
    leaves=[d for ds in partition(S,nodes,2,'x',4) for d in ds]
    for i,f in enumerate(fields):f['leaves']=leaves[16*i:16*(i+1)]
    assert all(d.rect[2:]==((4,5) if reagent=='G16' else (4,4)) for d in leaves)
