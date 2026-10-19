using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;
using System.Xml;
using System.Xml.Linq;
using Accessibility;

namespace TftPreview {
 // Pure state machine: a temporary non-editor page does not forget success.
 // A genuine A -> B -> A visit still merges A, since the preview may show B.
 public sealed class MergeState {
  string candidate = "", completed = ""; long since;
  public int StableMilliseconds = 400;
  public static string Token(string session, string key, string revision) {
   return session + "\n" + key + "\n" + revision;
  }
  public bool Observe(string token, long now) {
   if (token != candidate) { candidate = token; since = now; return false; }
   return token != completed && now - since >= StableMilliseconds;
  }
  public void Interrupt() { candidate = ""; }
  public void Confirm(string token) { completed = token; }
  public bool IsConfirmed(string token) { return completed == token; }
  public void Reset() { candidate = completed = ""; }
 }
 public sealed class Node {
  public IAccessible Acc; public string Name, Value, Scope; public int Role, State;
  public bool Ready { get { return (State & (1 | 32768 | 65536)) == 0; } }
  public void Invoke() {
   if (Role != 43 || !Ready) throw new InvalidOperationException("Button unavailable");
   Acc.accDoDefaultAction(0);
  }
 }
 public static class Access {
  static readonly Regex SectionPattern = new Regex(@"\.elab,\d+$", RegexOptions.IgnoreCase);
  [DllImport("oleacc.dll")] static extern int AccessibleObjectFromWindow(IntPtr h, uint id, ref Guid iid, [MarshalAs(UnmanagedType.Interface)] out IAccessible a);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
  [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr p);
  [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsWindowEnabled(IntPtr h);
  [DllImport("user32.dll")] static extern IntPtr GetAncestor(IntPtr h, uint flags);
  [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  delegate bool EnumProc(IntPtr h, IntPtr p);
  public static int Pid(IntPtr h) { uint p; GetWindowThreadProcessId(h, out p); return (int)p; }
  public static bool OwnedBy(IntPtr dialog, IntPtr main) { return GetAncestor(dialog, 3) == main; }
  public static List<IntPtr> Windows(int p) {
   var result = new List<IntPtr>();
   EnumWindows(delegate(IntPtr h, IntPtr x) { if (IsWindowVisible(h) && Pid(h) == p) result.Add(h); return true; }, IntPtr.Zero);
   return result;
  }
  public static string Title(IntPtr h) { var s = new StringBuilder(1024); GetWindowText(h, s, s.Capacity); return s.ToString(); }
  public static List<Node> Read(IntPtr h) {
   IAccessible a; Guid g = new Guid("618736E0-3C3D-11CF-810C-00AA00389B71");
   int rc = AccessibleObjectFromWindow(h, 0xFFFFFFFC, ref g, out a);
   if (rc != 0) Marshal.ThrowExceptionForHR(rc);
   var list = new List<Node>();
   try { Walk(a, 0, "", list); return list; } catch { Free(list); throw; }
  }
  static void Walk(IAccessible a, int depth, string scope, List<Node> list) {
   if (depth > 24 || list.Count >= 2000) { Release(a); throw new IOException("Accessibility tree incomplete"); }
   bool kept = false;
   try {
    int st = Convert.ToInt32(a.get_accState(0)), role = Convert.ToInt32(a.get_accRole(0));
    if ((st & 32768) != 0) return;
    string name = a.get_accName(0) ?? "";
    if (role == 10 && SectionPattern.IsMatch(name)) scope = name;
    string value = "";
    if (scope.Length != 0) { try { value = a.get_accValue(0) ?? ""; } catch (COMException) { } }
    list.Add(new Node { Acc = a, Name = name, Value = value, Role = role, State = st, Scope = scope }); kept = true;
    // Never enumerate the electrode/preview grid or use selection/position as a revision.
    if (role == 24 || role == 35 || role == 11) return;
    int count = a.accChildCount;
    if (count > 200) throw new IOException("Accessibility child list incomplete");
    for (int i = 1; i <= count; i++) {
     IAccessible child = null;
     try { child = a.get_accChild(i) as IAccessible; } catch (COMException) { }
     if (child != null) Walk(child, depth + 1, scope, list);
    }
   } finally { if (!kept) Release(a); }
  }
  static void Release(object x) { try { if (x != null && Marshal.IsComObject(x)) Marshal.ReleaseComObject(x); } catch { } }
  public static void Free(List<Node> nodes) { if (nodes != null) foreach (var n in nodes) Release(n.Acc); }
  public static Node OneButton(List<Node> nodes, string name) {
   Node found = null;
   foreach (var n in nodes) if (n.Role == 43 && n.Name == name && n.Ready) { if (found != null) return null; found = n; }
   return found;
  }
  public static string Section(List<Node> nodes) {
   string key = null;
   foreach (var n in nodes) if (n.Role == 10 && n.Ready && SectionPattern.IsMatch(n.Name)) {
    if (key != null && key != n.Name) return null; key = n.Name;
   }
   return key;
  }
  public static string GraphRevision(List<Node> nodes, string key) {
   var s = new StringBuilder();
   foreach (var n in nodes) if (n.Scope == key) s.Append(n.Role).Append('|').Append(n.Name).Append('|').Append(n.Value).Append('\n');
   return Revision.Hash(s.ToString());
  }
 }
 public static class Revision {
  public static string Hash(string text) {
   using (var h = SHA256.Create()) return Convert.ToBase64String(h.ComputeHash(Encoding.UTF8.GetBytes(text)));
  }
  public static string Flow(string xml, string id) {
   var settings = new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null, IgnoreWhitespace = true };
   using (var input = new StringReader(xml)) using (var reader = XmlReader.Create(input, settings)) {
    var doc = XDocument.Load(reader);
    foreach (var f in doc.Root.Elements("Flow")) if ((string)f.Attribute("ID") == id) return Hash(f.ToString(SaveOptions.DisableFormatting));
   }
   throw new IOException("Section not found in source");
  }
 }
 // Cache parsed flow hashes, not just the whole file hash: saving another flow
 // must not force an unchanged section to merge again.
 public sealed class SourceRevisions {
  string path = ""; long stamp = -1, length = -1;
  readonly Dictionary<string, string> flows = new Dictionary<string, string>();
  public string Read(string key) {
   int split = key.LastIndexOf(','); string file = key.Substring(0, split), id = key.Substring(split + 1);
   var info = new FileInfo(file);
   if (path != file || stamp != info.LastWriteTimeUtc.Ticks || length != info.Length) {
    long before = info.LastWriteTimeUtc.Ticks, size = info.Length;
    XDocument doc;
    var settings = new XmlReaderSettings { DtdProcessing = DtdProcessing.Prohibit, XmlResolver = null, IgnoreWhitespace = true };
    using (var stream = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.ReadWrite | FileShare.Delete))
    using (var reader = XmlReader.Create(stream, settings)) doc = XDocument.Load(reader);
    info.Refresh();
    if (before != info.LastWriteTimeUtc.Ticks || size != info.Length) throw new IOException("Source is being saved");
    flows.Clear();
    foreach (var f in doc.Root.Elements("Flow")) flows[(string)f.Attribute("ID")] = Revision.Hash(f.ToString(SaveOptions.DisableFormatting));
    path = file; stamp = before; length = size;
   }
   string result; if (!flows.TryGetValue(id, out result)) throw new IOException("Section not found in source");
   return result;
  }
 }
 public sealed class MergeWorker : IDisposable {
  readonly Action<string, string, bool> publish;
  readonly string log;
  readonly AutoResetEvent wake = new AutoResetEvent(false);
  readonly Thread thread;
  readonly MergeState state = new MergeState(); readonly SourceRevisions source = new SourceRevisions();
  readonly Stopwatch clock = Stopwatch.StartNew();
  volatile bool running = true, enabled; int generation; volatile int stableMs = 400;
  int Epoch { get { return Thread.VolatileRead(ref generation); } }
  string session = "", lastKey = "", editKey = "", pendingKey = "", pendingToken = "";
  int editVersion, pendingPid; IntPtr pendingMain; long pendingSince; bool editorDialog;
  public MergeWorker(string path, Action<string, string, bool> callback) {
   log = path; publish = callback;
   thread = new Thread(Loop); thread.IsBackground = true; thread.SetApartmentState(ApartmentState.STA); thread.Start();
  }
  public void Configure(bool on, int delay) { stableMs = delay; enabled = on; Interlocked.Increment(ref generation); wake.Set(); }
  void Log(string ev, string key) {
   try { File.AppendAllText(log, DateTime.Now.ToString("o") + "\t" + ev + "\t" + key + Environment.NewLine, Encoding.UTF8); } catch { }
  }
  void Show(string message, string key) { publish(message, key, false); }
  bool Allowed(int epoch, int pid) { return running && enabled && epoch == Epoch && Access.Pid(Access.GetForegroundWindow()) == pid; }
  void StopWithError(string message, string detail) {
   enabled = false; pendingPid = 0; state.Interrupt(); Log("ERROR", detail); publish(message, lastKey, true);
  }
  bool SuccessDialog(int epoch) {
   if (!Allowed(epoch, pendingPid)) return false;
   var wins = Access.Windows(pendingPid);
   if (!wins.Contains(pendingMain)) throw new IOException("Original TFT window closed");
   Node button = null; List<Node> owned = null;
   try {
    foreach (var h in wins) {
     if (h == pendingMain || !Access.OwnedBy(h, pendingMain)) continue;
     var nodes = Access.Read(h); bool success = false;
     foreach (var n in nodes) if (n.Role == 41) {
      string text = n.Name.Trim();
      if (text == "合并完成" || text == "合并完成!" || text == "合并完成！" || text == "Merge finished!") success = true;
     }
     var ok = success ? Access.OneButton(nodes, "确定") ?? Access.OneButton(nodes, "确定 Enter") ?? Access.OneButton(nodes, "OK") ?? Access.OneButton(nodes, "&OK") : null;
     if (ok != null) {
      if (owned != null) { Access.Free(nodes); return false; }
      owned = nodes; button = ok;
     } else Access.Free(nodes);
    }
    if (button == null || !Allowed(epoch, pendingPid)) return false;
    button.Invoke(); state.Confirm(pendingToken); Log("SUCCESS", pendingKey);
    Show("已合并；切换或修改 section 后自动更新", pendingKey); return true;
   } finally { Access.Free(owned); }
  }
  int Scan(int epoch) {
   if (pendingPid != 0) {
    if (!Allowed(epoch, pendingPid)) { Show("暂停：TFT 不在前台", pendingKey); return 400; }
    if (SuccessDialog(epoch)) { pendingPid = 0; return 200; }
    if (clock.ElapsedMilliseconds - pendingSince > 15000) {
     enabled = false; pendingPid = 0; state.Interrupt(); Log("UNCONFIRMED", pendingKey);
     publish("未确认合并成功，已停止。请查看原软件提示。", pendingKey, true);
    }
    return 200;
   }
   var ps = Process.GetProcessesByName("xPeLabTFTApp"); int pid = 0;
   try { if (ps.Length == 1) pid = ps[0].Id; } finally { foreach (var p in ps) p.Dispose(); }
   if (pid == 0) { state.Interrupt(); Show("等待唯一的 TFT 窗口", lastKey); return 600; }
   if (!Allowed(epoch, pid)) { state.Interrupt(); Show("暂停：TFT 不在前台", lastKey); return 400; }
   IntPtr main = IntPtr.Zero; var wins = Access.Windows(pid);
   foreach (var h in wins) if (Access.Title(h).EndsWith("- TFTApp")) { if (main != IntPtr.Zero) return 400; main = h; }
   if (main == IntPtr.Zero) { state.Interrupt(); return 400; }
   string currentSession = pid + ":" + main.ToInt64();
   if (session != currentSession) { session = currentSession; state.Reset(); lastKey = editKey = ""; editVersion = 0; editorDialog = false; }
   if (wins.Count != 1 || !Access.IsWindowEnabled(main)) {
    // A native editor dialog may change data not exposed by MSAA until it closes.
    // Conservatively re-merge once on return, even when that dialog was cancelled.
    if (!editorDialog && lastKey.Length != 0) { editKey = lastKey; editVersion++; }
    editorDialog = true;
    state.Interrupt(); Show("暂停：原软件对话框或调试窗口打开", lastKey); return 400;
   }
   editorDialog = false;
   List<Node> nodes = null;
   try {
    nodes = Access.Read(main);
    if (Access.OneButton(nodes, "连接") == null) { state.Interrupt(); Show("暂停：无法确认设备未连接", lastKey); return 400; }
    string key = Access.Section(nodes);
    if (key == null || Access.OneButton(nodes, "合并") == null) { state.Interrupt(); Show("等待 section 编辑页", lastKey); return 400; }
    if (key != lastKey) { lastKey = key; if (editKey != key) editKey = ""; }
    string revision = source.Read(key) + ":" + Access.GraphRevision(nodes, key) + ":" + (editKey == key ? editVersion : 0);
    string token = MergeState.Token(session, key, revision);
    state.StableMilliseconds = stableMs;
    if (!state.Observe(token, clock.ElapsedMilliseconds)) {
     bool unchanged = state.IsConfirmed(token);
     Show(unchanged ? "已合并；切换或修改 section 后自动更新" : "等待 section 稳定", key);
     return unchanged ? 600 : 200;
    }
    // Never reuse an accessible button captured before stabilization.
    Access.Free(nodes); nodes = null;
    if (!Allowed(epoch, pid) || Access.Windows(pid).Count != 1 || !Access.IsWindowEnabled(main)) { state.Interrupt(); return 200; }
    nodes = Access.Read(main);
    if (Access.Section(nodes) != key || source.Read(key) + ":" + Access.GraphRevision(nodes, key) + ":" + (editKey == key ? editVersion : 0) != revision) { state.Interrupt(); return 200; }
    var merge = Access.OneButton(nodes, "合并");
    if (merge == null || Access.OneButton(nodes, "连接") == null || !Allowed(epoch, pid)) { state.Interrupt(); return 200; }
    pendingPid = pid; pendingMain = main; pendingKey = key; pendingToken = token; pendingSince = clock.ElapsedMilliseconds;
    Log("MERGE", key); Show("正在合并当前 section", key); merge.Invoke(); return 200;
   } finally { Access.Free(nodes); }
  }
  void Loop() {
   Log("START", "v2 disabled"); int seen = -1;
   while (running) {
    int epoch = Epoch, delay = 400;
    if (seen != epoch) {
     seen = epoch; state.Reset(); pendingPid = 0; editKey = ""; editVersion = 0; editorDialog = false;
     Log(enabled ? "ENABLE" : "DISABLE", "stable_ms=" + stableMs);
    }
    if (enabled) {
     try { delay = Scan(epoch); }
     catch (XmlException ex) { state.Interrupt(); Show("等待脚本保存完成：" + ex.Message, lastKey); }
     catch (IOException ex) { state.Interrupt(); Show("等待脚本或窗口就绪：" + ex.Message, lastKey); }
     catch (Exception ex) { if (epoch == Epoch) StopWithError("检查失败，已禁用：" + ex.Message, ex.Message); }
    }
    wake.WaitOne(delay);
   }
   Log("EXIT", "");
  }
  public void Dispose() { enabled = false; running = false; Interlocked.Increment(ref generation); wake.Set(); }
 }
 public sealed class Panel : Form {
  readonly CheckBox enabled = new CheckBox(); readonly Label status = new Label(), section = new Label();
  readonly NumericUpDown delay = new NumericUpDown(); MergeWorker worker;
  public Panel(string logPath) {
   Text = "TFT 自动合并 v2"; ClientSize = new Size(460,190); AutoScaleMode = AutoScaleMode.Dpi;
   FormBorderStyle = FormBorderStyle.FixedToolWindow; MaximizeBox = false; TopMost = true; StartPosition = FormStartPosition.Manual;
   Location = new Point(30,160); Font = new Font("Microsoft YaHei UI",9);
   enabled.Text = "启用自动合并 / Enable"; enabled.SetBounds(16,12,420,28);
   var label = new Label { Text = "切换/修改后稳定等待（毫秒）", AutoSize = true, Location = new Point(16,48) };
   delay.SetBounds(272,44,110,26); delay.Minimum = 200; delay.Maximum = 2000; delay.Increment = 100; delay.Value = 400;
   status.SetBounds(16,82,428,40); section.SetBounds(16,124,428,25); section.AutoEllipsis = true;
   var hint = new Label { Text = "仅离线预览；不连接、不运行设备。关闭窗口即停止。", AutoSize = true, Location = new Point(16,163) };
   Controls.AddRange(new Control[] { enabled,label,delay,status,section,hint });
   status.Text = "已禁用；勾选上方开关启用";
   worker = new MergeWorker(logPath, UpdateStatus);
   enabled.CheckedChanged += delegate { worker.Configure(enabled.Checked,(int)delay.Value); status.Text = enabled.Checked ? "已启用，等待 section 稳定" : "已禁用；保留手动合并"; };
   delay.ValueChanged += delegate { worker.Configure(enabled.Checked,(int)delay.Value); };
   FormClosed += delegate { worker.Dispose(); };
  }
  void UpdateStatus(string message, string key, bool disable) {
   if (IsDisposed || !IsHandleCreated) return;
   try { BeginInvoke((MethodInvoker)delegate {
    if (IsDisposed) return;
    if (disable) enabled.Checked = false;
    // Ignore a late worker result after the user has disabled the feature.
    if (!enabled.Checked && !disable) return;
    status.Text = message; section.Text = Path.GetFileName(key);
   }); } catch (InvalidOperationException) { }
  }
 }
 public static class Entry {
  [STAThread] public static void Run(string logPath) {
   Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false); Application.Run(new Panel(logPath));
  }
  public static string Inspect() {
   var ps = Process.GetProcessesByName("xPeLabTFTApp");
   try {
    if (ps.Length != 1) return "WAIT: expected one TFT process";
    foreach (var h in Access.Windows(ps[0].Id)) if (Access.Title(h).EndsWith("- TFTApp")) {
     var nodes = Access.Read(h);
     try {
      string key = Access.Section(nodes);
      return "SECTION=" + key + " MERGE=" + (Access.OneButton(nodes,"合并") != null) + " OFFLINE=" + (Access.OneButton(nodes,"连接") != null) +
       " GRAPH=" + (key == null ? "" : Access.GraphRevision(nodes,key));
     } finally { Access.Free(nodes); }
    }
    return "WAIT: TFT editor not open";
   } finally { foreach (var p in ps) p.Dispose(); }
  }
 }
}
