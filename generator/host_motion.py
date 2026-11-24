"""Prepare resident samples concurrently with independent top-band reagent jobs."""
import rigid as R
class HostMotion:
 def __init__(self,S,targets):
  self.previous=getattr(S,'background',None)
  self.items=[(S.get(n),R.go(S.get(n),[('x',x)])) for n,x in targets.items()]
  self.events=[];S.background=self.advance
 def advance(self,S):
  result=self.previous(S) if self.previous else ([],[],0)
  if not self.items:return result
  old=getattr(S,'moved',0);before={id(d):d.rect for d,g in self.items}
  changed,touch,ms=R.tick(S,self.items,record=False)
  for d,g in self.items:
   if d.rect!=before[id(d)]:self.events.append(dict(frame=S.nsteps,name=d.name,rect=list(d.rect)))
  for d,g in list(self.items):
   if getattr(d,'pending',None)=='done':self.items.remove((d,g));d.pending=None
  S.moved=old
  return result[0]+changed,result[1]+touch,max(result[2],ms)
