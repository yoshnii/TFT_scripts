"""Position retained reaction liquid during independent waste traffic."""
import rigid as R


def start(S,items,tag,target):
    import waste_grid as W
    W._pool(S)
    previous=S.background
    S.retained_ready_events=getattr(S,'retained_ready_events',[])
    def advance(scene):
        changed,touch,ms=previous(scene)
        if not S.waste_pool.items:return changed,touch,ms
        for ln,_ in items:
            d=scene.get(tag+ln.id if tag else ln.name);x=ln.X0+target
            if d.rect[0]==x:continue
            before=d.rect;rc=(before[0]+(1 if x>before[0] else -1),*before[1:])
            if R.rect_ok(scene,d.name,rc):
                d.rect=rc;changed.append(d.name)
                S.retained_ready_events.append(dict(frame=S.nsteps,name=d.name,before=list(before),after=list(rc),
                    waste_in_flight=bool(S.waste_pool.items)))
        return changed,touch,ms
    S.background=advance
    return previous
