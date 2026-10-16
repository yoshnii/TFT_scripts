"""出口原位等分；竖向完整子滴原形下行，底部整排自然汇液。"""
import geom
from scene import Drop
import ops
from native_packets import partition


def schedule(round_count,w,h,kind):
    """只在真实手工吸液点分批；同批所有F孔并行准备。"""
    if kind=='R04':stops=[(3,[4,3,2,1],12),(2,[4,3,2,1],8)]
    elif kind=='C04':stops=[(4,[4,3,2,1],16),(4,[4,3,2,1],16)]
    elif kind=='A05':stops=[(2,[3,1],16)]
    else:stops=[(round_count,list(range(round_count,0,-1)),16)]
    plans=[]
    assert sum(n for n,p,c in stops)==round_count
    for stop,(nround,port_ids,per_port) in enumerate(stops,1):
        ports=len(port_ids)
        assert ports<=4 and nround*16==ports*per_port
        specs={};assign=[]
        # 固定同高小滴自然排成完整行列，第一批先远孔后近孔。
        columns=8 if kind=='A05' and (w,h)!=(4,4) else 4
        row_h=4 if kind in ('C04','cleanup') else h
        W=w*columns if row_h==h else w*columns*h//row_h
        rows=per_port//columns
        for k in port_ids:
            key=(stop,k);specs[key]=dict(port=k,W=W,
                rows=[([w]*columns,h,row_h)]*rows,native=True)
            if kind=='A05' and (w,h)==(4,4):specs[key]['a06_compact']=True
            assign.extend((key,i//columns,i%columns) for i in range(per_port))
        for r in range(nround):
            chunk=assign[r*16:(r+1)*16];keys=list(dict.fromkeys(k for k,row,col in chunk))
            plans.append(dict(stop=stop,flush=r==nround-1,ports=[specs[k]['port'] for k in keys],
                assign=chunk,specs=specs,minimum_ports=ports,packed=True))
    return plans


def bites(S,items,blobs,final=True,pellets=None,collect_three=False,collect_a05=False,single_stop=False):
    import rigid as R
    import waste_grid as W
    items=list(items);assert len(items)==16
    assert not any(blobs.values()),'新废液批次前必须已吸走旧废液'
    assert all(d.vol>0 for ln,d in items),'磁珠必须加入真实载液后才能排走'
    n=len(items[0][1].target_cells());assert all(len(d.target_cells())==n for ln,d in items)
    if n==100:kind='R04';round_count=5;w,h=4,5
    elif n==120:kind='C04';round_count=8;w,h=3,5
    elif n==32 and collect_a05:kind='A05';round_count=2;w,h=4,4
    elif n in (30,60):kind='A05' if n==30 else 'cleanup';round_count=n//15;w,h=3,5
    elif n==12:kind='barcode';round_count=1;w,h=3,4
    else:raise ValueError(f'未审核的等体积排废计划：{n}格')
    gx=geom.gap_x(geom.waste_gap(items[0][0].c));plans=schedule(round_count,w,h,kind)
    if single_stop:assert sum(p['flush'] for p in plans)==1,'整步废液无法安全一次装入F1–F4'
    if n>w*h:
        # 仅整块母液按最终竖向叶子的行高定形；不让整高母液横穿S孔。
        height=4 if n==32 and collect_a05 else 5
        R.reshape_many(S,[(d,(d.rect[0],ln.Y0,n//height,height)) for ln,d in items
                          if d.rect[2:]!=(n//height,height)])
    stacks={ln.id:[d] for ln,d in items};jobs={}
    S.waste_schedule_events=getattr(S,'waste_schedule_events',[])
    S.waste_schedule_events.append(dict(kind=kind,packet_areas=[w*h]*round_count,
        actual_packet_ul=items[0][1].vol/round_count,schedule=plans,step=S.nsteps,
        equal_volume=True,no_packet_reshape=True))
    item_names={d.name for ln,d in items};fixed={}
    for ln,d in items:
        fixed[ln.id]=max((q.rect[0]+q.rect[2] for q in S.drops.values()
            if q.name not in item_names and q.vol>0 and q.rect[1]>=ln.Y0
            and q.rect[1]<ln.Y0+5 and ln.X0<=q.rect[0]<ln.X0+36),default=ln.X0-geom.CUT_GAP)
        if kind=='C04':d.native_fractional_volume=True
    if kind=='R04':
        # 一次同量多界面分区；不顺序剥取1µL/4µL等不等量小尾滴。
        ds=[d for ln,d in items]
        assert all(d.rect[2:]==(20,5) for d in ds)
        groups=partition(S,ds,5,'x',3)
        for (ln,d),children in zip(items,groups):stacks[ln.id]=list(children)
    for index in range(round_count):
        while len(stacks[items[0][0].id][-1].target_cells())>w*h:
            active=[(ln,stacks[ln.id][-1]) for ln,d in items]
            targets={d.name:(d.rect[0] if kind=='A05' and n==32 else ln.X0+36-d.rect[2])
                     for ln,d in active}
            assert all(targets[d.name]>=d.rect[0] for ln,d in active),'不得将出口母滴退回左侧切割站'
            R.run(S,[(d,R.go(d,[('x',targets[d.name])])) for ln,d in active])
            groups=partition(S,[d for ln,d in active],2,'x',geom.CUT_GAP)
            for (ln,d),children in zip(active,groups):stacks[ln.id][-1:]=children
            if len(groups[0][1].target_cells())>w*h:
                # 只给尚需再分的右支让空间；移动左兄弟而不是退回待切母滴。
                moves={}
                for ln,d in items:
                    x=max(ln.X0,fixed[ln.id]+geom.CUT_GAP)
                    for sibling in stacks[ln.id][:-1]:
                        moves[sibling.name]=x;x+=sibling.rect[2]+geom.CUT_GAP
                ops.move_rects(S,moves)
        pieces=[]
        for ln,d in items:
            child=stacks[ln.id].pop()
            assert child.rect[2:]==(w,h),(kind,child.rect,(w,h))
            child.native_packet=True;child.native_leaf_shape=(w,h)
            pieces.append((ln,child))
        plan=plans[index]
        tiles=W._send_pipeline(S,pieces,gx,blobs,plan,jobs)
        if plan['flush']:
            W._drain(S,lambda:not S.waste_pool.items)
            W.flush(S,blobs)
        else:W._drain(S,lambda:all(S.drops.get(d.name) is not d or d.rect[1]>=W.TRANSPORT_Y for d in tiles))
    assert not any(stacks.values())
    if pellets:
        for ln,d in items:
            p=pellets[ln.id];S.remove(p.name);p.name=ln.name;p.allow=();p.magnet_only=True;S.add(p)


def egress_pieces(S,items,blob_state,final=True,collect_a05=False,single_stop=False):
    items=list(items)
    assert all(d.vol>0 and not getattr(d,'magnet_only',False) for ln,d in items),'磁珠排废必须先加入真实载液'
    accepted=(12,30,32,60,100,120) if collect_a05 else (12,30,60,100,120)
    if not final or len(items)!=16 or any(len(d.target_cells()) not in accepted for ln,d in items):
        # 本轮只替换正式单列方案；保留独立归一化等旧试验的接口，不扩大改动范围。
        import waste_grid
        return waste_grid.egress_pieces(S,items,blob_state,final,collect_a05,single_stop)
    return bites(S,items,blob_state,final,collect_a05=collect_a05,single_stop=single_stop)


def supernatant_bites(S,lanes,blob_state,pellet_vol,final=True,collect_three=False):
    assert pellet_vol==0
    lanes=list(lanes);items=[];pellets={}
    for ln in lanes:
        d=S.get(ln.name);S.remove(d.name);d.name='LIQ'+ln.id;S.add(d)
        p=S.add(Drop('PEL'+ln.id,ln.rect(ln.mag-1,3),0));p.magnet_only=True
        d.allow=(p.name,);pellets[ln.id]=p;items.append((ln,d))
    return bites(S,items,blob_state,final,pellets,collect_three=collect_three)
