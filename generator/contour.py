"""整格连通轮廓：面积=计划液量/0.05；近矩形边界逐帧协同调整。"""
from collections import defaultdict
import geom

def neighbors(p):
    x,y=p
    return ((x-1,y),(x+1,y),(x,y-1),(x,y+1))

def connected(cells):
    if not cells:return False
    seen={next(iter(cells))};todo=list(seen)
    while todo:
        for q in neighbors(todo.pop()):
            if q in cells and q not in seen:seen.add(q);todo.append(q)
    return len(seen)==len(cells)

def fill(rect,area):
    x,y,w,h=rect
    assert 0<area<=w*h,(rect,area,'承接范围不足，不能缩小计划体积')
    return frozenset((x+i%w,y+i//w) for i in range(area))

def rectangles(cells):
    rows=defaultdict(list)
    for x,y in cells:rows[y].append(x)
    runs=[]
    for y,xs in sorted(rows.items()):
        xs=sorted(xs);start=last=xs[0]
        for x in xs[1:]:
            if x==last+1:last=x;continue
            runs.append((start,y,last-start+1,1));start=last=x
        runs.append((start,y,last-start+1,1))
    out=[]
    for x,y,w,h in runs:
        match=next((i for i,r in enumerate(out) if r[0]==x and r[2]==w and r[1]+r[3]==y),None)
        if match is None:out.append((x,y,w,1))
        else:a,b,c,d=out[match];out[match]=(a,b,c,d+1)
    return out

def pixel_morph(source,target):
    source=frozenset(source);target=frozenset(target)
    assert len(source)==len(target) and connected(source) and connected(target),'轮廓形变须等面积、连通'
    cur=source
    while cur!=target:
        adds=sorted(p for p in target-cur if any(q in cur for q in neighbors(p)))
        removes=sorted(cur-target)
        found=None;score=None
        for add in adds:
            for remove in removes:
                nxt=(cur|{add})-{remove}
                # 优先沿整条矩形边界收展，不能只保证连通却长出细枝。
                if not orthoconvex(nxt) or not has_corner(nxt):continue
                perimeter=sum(4-sum(q in nxt for q in neighbors(p)) for p in nxt)
                box=bounds(nxt);box_area=box[2]*box[3]
                candidate=(perimeter,box_area,abs(box[2]-box[3]),add,remove)
                if score is None or candidate<score:found=frozenset(nxt);score=candidate
        if found is None:
            # 合并的原始接触轮廓可能暂有凹角，仍要求连通且优先减少周长。
            for add in adds:
                for remove in removes:
                    nxt=(cur|{add})-{remove}
                    if not connected(nxt) or not has_corner(nxt):continue
                    perimeter=sum(4-sum(q in nxt for q in neighbors(p)) for p in nxt)
                    candidate=(perimeter,bounds(nxt)[2]*bounds(nxt)[3],add,remove)
                    if score is None or candidate<score:found=frozenset(nxt);score=candidate
        assert found is not None,('无法用单格交换形成目标轮廓',rectangles(cur),rectangles(target))
        assert len(found)==len(source) and len(found^cur)==2
        cur=found;yield cur

def near_rectangle(cells):
    if not orthoconvex(cells) or not has_corner(cells):return False
    rows=defaultdict(int);cols=defaultdict(int)
    for x,y in cells:rows[y]+=1;cols[x]+=1
    return max(rows.values())-min(rows.values())<=1 or max(cols.values())-min(cols.values())<=1

def edge_change(a,b):
    x,y,w,h=bounds(a);u,v,s,t=bounds(b)
    return max(abs(x-u),abs(y-v),abs(x+w-u-s),abs(y+h-v-t))

def morph(source,target):
    """参考矩形长宽协同调整：每条外边界最多移动1格，不生长十字细枝。"""
    source=frozenset(source);target=frozenset(target);n=len(source)
    assert n==len(target) and connected(source) and connected(target)
    cur=source;limit=max(2,n//2)
    while cur!=target:
        if (edge_change(cur,target)<=1 and len(cur^target)<=limit
                and (near_rectangle(target) or len(cur^target)<=2)):
            cur=target;yield cur;continue
        x,y,w,h=bounds(cur);old_overlap=len(cur&target);choices=[]
        for left in range(x-1,x+2):
            for right in range(x+w-1,x+w+2):
                for top in range(y-1,y+2):
                    for bottom in range(y+h-1,y+h+2):
                        W,H=right-left,bottom-top
                        if W<=0 or H<=0 or W*H<n:continue
                        # 矩形边缘可有一格台阶，但不是从中间抽出长细枝。
                        candidates=[fill((left,top,W,H),n)]
                        if H<=n:
                            q,r=divmod(n,H)
                            if 0<q<=W and q+(r>0)<=W:
                                candidates.append(frozenset((left+i,top+j) for j in range(H) for i in range(q+(j<r))))
                        if W<=n:
                            q,r=divmod(n,W)
                            if 0<q<=H and q+(r>0)<=H:
                                candidates.append(frozenset((left+i,top+j) for i in range(W) for j in range(q+(i<r))))
                        for cells in candidates:
                            overlap=len(cells&target)
                            if overlap<=old_overlap or len(cur^cells)>limit or edge_change(cur,cells)>1:continue
                            if not near_rectangle(cells):continue
                            perimeter=sum(4-sum(q in cells for q in neighbors(p)) for p in cells)
                            choices.append(((-overlap,perimeter,len(cur^cells)),cells))
        if choices:
            cur=min(choices,key=lambda p:p[0])[1];yield cur
        else:
            # 特殊拆分分区的一两格凹口：保持面积，逐格完成，不跨空白区。
            nxt=next(pixel_morph(cur,target))
            assert edge_change(cur,nxt)<=1
            cur=nxt;yield cur

def orthoconvex(cells):
    """每行每列必须是一段连续电极；排除洞、回折和多段细枝。"""
    rows=defaultdict(list);cols=defaultdict(list)
    for x,y in cells:rows[y].append(x);cols[x].append(y)
    return all(max(v)-min(v)+1==len(v) for v in (*rows.values(),*cols.values())) and connected(cells)

def has_corner(cells):
    x,y,w,h=bounds(cells)
    return any(p in cells for p in ((x,y),(x+w-1,y),(x,y+h-1),(x+w-1,y+h-1)))

def compact(area,max_w=50,max_h=20,aspect=2):
    choices=[(w,area//w) for w in range(1,max_w+1) if area%w==0
             and area//w<=max_h and max(w,area//w)/min(w,area//w)<=aspect]
    if not choices:return None
    return min(choices,key=lambda q:(max(q)/min(q),q[0]))

def compact_parts(area):
    """不能组成紧凑矩形的总格数，分成两个整格紧凑小框；不增加液量。"""
    if compact(area):return [area]
    pairs=[(a,area-a) for a in range(1,area//2+1) if compact(a) and compact(area-a)]
    assert pairs,('无法分成两个孔口紧凑框',area)
    return list(min(pairs,key=lambda p:abs(p[0]-p[1])))

def bounds(cells):
    xs=[p[0] for p in cells];ys=[p[1] for p in cells]
    return min(xs),min(ys),max(xs)-min(xs)+1,max(ys)-min(ys)+1
