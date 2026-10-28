"""Independent in-well preparation, sharing the existing fixed F receivers.

Every row keeps its binary tree, shapes, opening gap and dwell. A row can
prepare its next branch once its own prior packet has left that well.
"""
import geom,rigid as R
from scene import Drop


def move(S,d,x):
    while d.rect[0]!=x:
        def action(d=d,x=x):
            rc=(d.rect[0]+(1 if x>d.rect[0] else -1),*d.rect[1:])
            if not R.rect_ok(S,d.name,rc):return None
            d.rect=rc
            return 1000,[d.name],[]
        yield action


def shape(S,d,rc,area=None):
    def action():
        if not R.rect_ok(S,d.name,rc):return None
        d.electrode_area=area or rc[2]*rc[3]
        d.margin_budget=d.electrode_area-geom.cells(d.vol)
        d.native_authorized_padding=True;d.native_fractional_volume=True;d.rect=rc
        return 4000,[d.name],[]
    yield action


def split(S,p,gap,times):
    children=[];event={};pairs=[]
    def start():
        x,y,w,h=p.rect;assert w%2==0
        S.remove(p.name)
        for j in range(2):
            d=S.add(Drop(p.name+'_'+str(j),(x+j*w//2,y,w//2,h),p.vol/2))
            d.electrode_area=w//2*h;d.margin_budget=d.electrode_area-geom.cells(d.vol)
            d.native_authorized_padding=getattr(p,'native_authorized_padding',False)
            d.native_fractional_volume=getattr(p,'native_fractional_volume',False)
            d.allow=tuple(getattr(p,'allow',()))
            children.append(d)
        pairs.append(tuple(d.name for d in children))
        pairs.extend((d.name,n) for d in children for n in d.allow)
        event.update(parent=p.name,children=[d.name for d in children],axis='x',area=w*h,
            volumes=[d.vol for d in children],arity=2,partition_frame=S.nsteps,
            partition_preserves_mother_cells=True,_parent_key=id(p),_child_keys=[id(d) for d in children],
            gaps=[],positions=[],separation_frames=[],requested_frame_ms=list(times))
        S.split_events.append(event)
        return record(0)
    def record(i):
        event['gaps'].append(2*i);event['positions'].append([list(d.rect) for d in children])
        event['separation_frames'].append(S.nsteps)
        return times[i],[d.name for d in children],pairs
    yield start
    for i in range(1,1+(gap+1)//2):
        def opening(i=i):
            targets=[(d.rect[0]+sign,*d.rect[1:]) for d,sign in zip(children,(-1,1))]
            if not all(R.rect_ok(S,d.name,rc,frozenset(q.name for q in children)) for d,rc in zip(children,targets)):return None
            for d,rc in zip(children,targets):d.rect=rc
            return record(i)
        yield opening
    return children


def run(S,generators):
    S.split_events=getattr(S,'split_events',[])
    jobs=[dict(g=g,pending=None,ready=0,done=False) for g in generators]
    now=0;stall=0
    S.row_ready_events=getattr(S,'row_ready_events',[])
    while not all(j['done'] for j in jobs):
        changed=[];touch=[];started=[]
        for j in jobs:
            if j['done'] or now<j['ready']:continue
            if j['pending'] is None:
                try:j['pending']=next(j['g'])
                except StopIteration:j['done']=True;continue
            result=j['pending']()
            if result is None:continue
            ms,names,pairs=result
            changed+=names;touch+=pairs;j['pending']=None;started.append((j,ms))
        if all(j['done'] for j in jobs):break
        # A split's close siblings remain explicitly exempt until separated;
        # the exemption belongs only to those two actual equal halves.
        for e in S.split_events:
            names=e['children']
            if all(n in S.drops for n in names):
                a,b=(S.get(n) for n in names)
                from scene import _gap
                if _gap(a.rect,b.rect)<(6 if getattr(S,'aqueous_wait_gap',None) else 4):touch.append(tuple(names))
        before=S.nsteps;S.step(1000,changed=changed,touching=touch)
        actual=sum(r.dur for r in S.relays[-(S.nsteps-before):])
        for j,ms in started:j['ready']=now+max(ms,actual)
        now+=actual
        stall=0 if started or getattr(getattr(S,'waste_pool',None),'moved_last',False) else stall+1
        assert stall<100,('按行排废未能前进',[(j['ready'],j['pending']) for j in jobs if not j['done']],[(d.name,d.rect,getattr(d,'pending',None),getattr(d,'batch_round',None)) for d in S.drops.values() if d.vol>0])


def quarter(S,items,branches,plans,start_round,gx,blobs,jobs,kind,expanded,gap,cut_gap,times):
    import fixed_waste_collect as F
    import waste_grid as W
    times=times or (1000,)*(1+(cut_gap+1)//2)
    waiting={i:[] for i in range(4)}
    def row(ln,d):
        def station(d,width):
            ends=[q.rect[0]+q.rect[2] for q in S.drops.values() if q is not d and q.vol>0
                  and ln.Y0-1<=q.rect[1]<ln.Y0+5 and ln.X0<=q.rect[0]<ln.X0+36]
            delta=(cut_gap+1)//2
            minimum=max(ln.X0+delta,max(ends,default=ln.X0-gap)+gap+delta)
            choices=[x for x in range(minimum,ln.X0+36-width-delta+1) if x+width//2!=ln.mag]
            return min(choices) if choices else None
        x=station(d,16);assert x is not None,('切分站不足',ln.id)
        if d.rect[3]==6 or expanded and len(d.target_cells())==65:
            yield from shape(S,d,(d.rect[0],ln.Y0,16,4),64)
        yield from move(S,d,x)
        yield from shape(S,d,(x,ln.Y0,16,4),64)
        halves=yield from split(S,d,cut_gap,times)
        ordinal=0
        for side in (1,0):
            mother=halves[side]
            if expanded and side==1:
                sibling=halves[0]
                yield from shape(S,sibling,(*sibling.rect[:2],7,5),35)
            # Wait for this row's previous small droplets, rather than for
            # the entire column. Their gap checks still run during entry.
            def available():
                from scene import _gap
                x=station(mother,8)
                if x is None:return None
                sweep=(x-3,ln.Y0,14,4)
                if any(getattr(q,'continuous_waste_exit',False) and _gap(sweep,q.rect)<6 for q in S.drops.values()):return None
                return 1,[],[]
            yield available
            target=station(mother,8)
            yield from move(S,mother,target)
            if expanded and side==0:yield from shape(S,mother,(*mother.rect[:2],8,4),32)
            leaves=yield from split(S,mother,cut_gap,times)
            for child in (leaves[1],leaves[0]):
                def enqueue(child=child,ordinal=ordinal):
                    child.native_packet=True;child.native_leaf_shape=(4,4)
                    plan=dict(plans[start_round+ordinal],early_exit=(kind=='cleanup' or kind=='R04' and getattr(S,'r05_parallel',False)))
                    waiting[ordinal].append((ln,child))
                    if len(waiting[ordinal])==len(items):
                        F.send(S,waiting[ordinal],gx,blobs,plan,jobs,record=False)
                    S.row_ready_events.append(dict(row=ln.r,round=start_round+ordinal,frame=S.nsteps))
                    return 1000,[],[]
                yield enqueue
                ordinal+=1
                def clear(child=child):return (1,[],[]) if S.drops.get(child.name) is not child or child.rect[0]>=gx else None
                yield clear
    run(S,[row(ln,d) for (ln,_),d in sorted(zip(items,branches),key=lambda z:-z[0][0].r)])
    W._drain(S,lambda:not S.waste_pool.items);W.flush(S,blobs)
