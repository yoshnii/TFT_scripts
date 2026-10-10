"""把 anim_data.json 压缩后嵌进模板: python3 make_html.py <anim_data.json> <输出.html> [页面标题]"""
import sys, gzip, base64
data = open(sys.argv[1], "rb").read()
b64 = base64.b64encode(gzip.compress(data, 9)).decode()
html = open("anim_template.html", encoding="utf-8").read().replace("__DATA__", b64)
if len(sys.argv) > 3:
    html = html.replace("<title>PTseq SIRO96 片上回放</title>", f"<title>{sys.argv[3]}</title>").replace("<h1>PTseq SIRO96 片上回放</h1>", f"<h1>{sys.argv[3]}</h1>")
open(sys.argv[2], "w", encoding="utf-8").write(html)
print(f"{sys.argv[2]}: 数据 {len(data)/1e6:.1f} MB → gzip+base64 {len(b64)/1e6:.2f} MB, 页面 {len(html.encode())/1e6:.2f} MB")
