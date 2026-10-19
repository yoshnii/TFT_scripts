namespace TftPreview {
 public static class Tests {
  static int count;
  static void Check(bool ok, string label) { if (!ok) throw new System.Exception("FAIL: " + label); count++; }
  public static string Run() {
   count = 0;
   var s = new MergeState();
   string a = MergeState.Token("p1", "A", "v1"), b = MergeState.Token("p1", "B", "v1");
   Check(!s.Observe(a,0), "first observation waits");
   Check(!s.Observe(a,399), "debounce incomplete");
   Check(s.Observe(a,400), "stable section ready");
   s.Confirm(a); Check(!s.Observe(a,800), "no duplicate after success");
   s.Interrupt(); Check(!s.Observe(a,900), "page bounce restabilizes");
   Check(!s.Observe(a,1400), "page bounce does not duplicate");
   s.Observe(b,1500); Check(s.Observe(b,1900), "new section merges");
   s.Confirm(b); s.Observe(a,2000); Check(s.Observe(a,2400), "real return restores A preview");
   s.Confirm(a); string edited = MergeState.Token("p1","A","v2");
   s.Observe(edited,2500); Check(s.Observe(edited,2900), "same-section edit merges");
   s.Confirm(edited); Check(!s.Observe(edited,3000), "edited version merged once");
   s.Observe(b,3100); s.Observe(a,3200); s.Observe(edited,3300);
   Check(!s.Observe(edited,3800), "rapid switch back to completed version cancels candidate");
   string fresh = MergeState.Token("p2","A","v2");
   s.Observe(fresh,4000); Check(s.Observe(fresh,4400), "new app session invalidates cache");
   s.Confirm(fresh); s.Reset(); s.Observe(fresh,4500); Check(s.Observe(fresh,4900), "enable resets cache");
   var shortWait = new MergeState { StableMilliseconds = 200 };
   shortWait.Observe(a,0); Check(shortWait.Observe(a,200), "configurable stabilization");
   var xml = "<Script><Flow ID='1'><Command Params='old'/></Flow><Flow ID='2' Name='x'/></Script>";
   string hash = Revision.Flow(xml,"1");
   Check(hash != Revision.Flow(xml.Replace("old","new"),"1"), "saved parameter edit detected");
   Check(hash == Revision.Flow(xml.Replace("Name='x'","Name='y'"),"1"), "other section save ignored");
   Check(hash == Revision.Flow(xml.Replace("><",">\n  <"),"1"), "formatting does not trigger");
   bool dtdBlocked = false;
   try { Revision.Flow("<!DOCTYPE Script [<!ENTITY e SYSTEM 'file:///not-read'>]>" + xml,"1"); }
   catch (System.Xml.XmlException) { dtdBlocked = true; }
   Check(dtdBlocked, "external entities blocked");
   var nodes = new System.Collections.Generic.List<Node>();
   nodes.Add(new Node { Scope="A",Role=10,Name="canvas",Value="",State=0 });
   nodes.Add(new Node { Scope="A",Role=43,Name="command",Value="1",State=0 });
   string graph = Access.GraphRevision(nodes,"A"); nodes[1].State=2;
   Check(graph == Access.GraphRevision(nodes,"A"), "selection does not change revision");
   nodes[1].Value="2"; Check(graph != Access.GraphRevision(nodes,"A"), "graph value change detected");
   graph=Access.GraphRevision(nodes,"A");
   nodes.Add(new Node { Scope="",Role=42,Name="preview progress",Value="12",State=0 });
   Check(graph == Access.GraphRevision(nodes,"A"), "preview progress excluded");
   var file=System.IO.Path.Combine(System.IO.Path.GetTempPath(),System.Guid.NewGuid().ToString()+".elab");
   try {
    System.IO.File.WriteAllText(file,xml);
    var cache=new SourceRevisions(); Check(cache.Read(file+",1")==hash,"source cache reads section");
    System.IO.File.WriteAllText(file,xml.Replace("old","changed"));
    Check(cache.Read(file+",1")!=hash,"source cache reloads changed file");
   } finally { System.IO.File.Delete(file); }
   return "PASS: " + count + " AutoMerge local tests (no GUI invocation, no hardware action)";
  }
 }
}
