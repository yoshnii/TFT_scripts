"""Geometry and delivery policies for the 4-column, 16-row TFT."""
import geom
import transport_spacing
import top_horizontal

COLUMNS = (22, 81, 139, 198)
MODEL = 'ChipArea_192_256_64_SIRO_4col'


def configure():
    geom.COLS = len(COLUMNS)
    geom.physical_zone = lambda c,r: (COLUMNS[c-1], 27+9*(r-1))
    geom.gap_x = lambda k: COLUMNS[k-1]+36 if k else COLUMNS[0]-4
    geom.REACTION_WELLS = tuple((x+d-1,25+9*r,2,2)
        for x in COLUMNS for r in range(16) for d in (13,23))
    transport_spacing._well.cache_clear()
    top_horizontal.PLANS['G14'] = (4,8,1.25,200,{200:(20,10),100:(20,5),50:(10,5)},(5,5),2)

    top_horizontal.PLANS['LIG'] = (2,8,1.4,224,{224:(28,8),112:(28,4),56:(14,4)},(7,4),1)
    top_horizontal.PLANS['G16'] = (2,16,.88,320,{320:(32,10),160:(32,5),80:(20,4),40:(10,4)},(5,4),2)
    from functools import lru_cache
    import rigid
    for module, names in ((geom, ('between_lanes', 'reaction_holes_ok', 'keep_out_ok')), (rigid, ('in_reaction',))):
        for name in names:
            setattr(module, name, lru_cache(maxsize=65536)(getattr(module,name)))


def wash_channels(flow):
    """Two closed wash cycles reach both used corridors, with neighbouring rows out of phase."""
    from six_column_schedule import frames, write_frames
    lead,cycle,timer=flow
    previous=frames(lead)
    left=COLUMNS[0]-16      # R02 outer rail
    right=COLUMNS[0]+28     # full 12-cell footprint reaches the waste corridor
    centre=COLUMNS[0]+8
    setting,loaded=previous[0]
    assert set(loaded)=={(COLUMNS[0]+7,28+9*r,12,5) for r in range(16)}
    def state(offset):
        x_pair=[max(left,min(right,centre+sign*offset)) for sign in (1,-1)]
        if any(x<COLUMNS[0] or x+12>COLUMNS[0]+36 for x in x_pair):
            assert abs(x_pair[0]-x_pair[1])>=12+6
        return setting,frozenset((x_pair[r%2],28+9*r,12,5) for r in range(16))
    write_frames(lead,[previous[0],state(0)])
    sweep=list(range(25))+list(range(23,-25,-1))+list(range(-23,1))
    states=[state(t) for t in sweep]
    assert states[0]==states[-1] and int(timer.get('Timeout'))==2*int(cycle.get('Timeout'))
    for row in (0,1):
        xs=[next(x for x,y,w,h in masks if y==28+9*row) for _,masks in states]
        assert min(xs)==left and max(xs)==right and all(abs(i-j)<=1 for i,j in zip(xs,xs[1:]))
    write_frames(cycle,states)
    timer.set('Timeout',str(2*len(states)))
    timer.set('Name',f'两轮清洗计时 {2*len(states)}s（同步结束）')
    lead.set('Name','[外部磁控]磁铁下降后，新液在反应区内就位')
    cycle.set('Name','奇偶行反向往返 · 每轮覆盖双路进液和排废通道')
    flow.set('Timeout',str(2+2*len(states)))
    return flow
