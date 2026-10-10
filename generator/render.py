"""把工程里某条指令的若干步画成 PNG (纯 Python, 无依赖): python3 render.py <工程目录> <输出.png> <文件>:<Flow关键词>:<指令关键词>:<步号,步号,...> ..."""
import re, sys, zlib, struct, geom
SC = 4                                   # 每格像素
def png(path, W, H, pix):
    raw = b"".join(b"\x00" + bytes(pix[y]) for y in range(H))
    def chunk(t, d): return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    open(path, "wb").write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", W, H, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
def steps_of(prj, fname, flow_kw, cmd_kw):
    d = open(f"{prj}/{fname}.elab", encoding="utf-8").read()
    for m in re.finditer(r'<Flow ID="(\d+)" Name="([^"]*)"[^>]*>(.*?)</Flow>', d, re.S):
        if flow_kw not in m.group(2): continue
        for cm in re.finditer(r'<Command\b([^>]*?)>(.*?)</Command>', m.group(3), re.S):
            name = re.search(r'Name="([^"]*)"', cm.group(1)).group(1)
            if cmd_kw in name: return name, re.findall(r'RelayCode="([^"]*)"', cm.group(2))
    return None, []
def canvas():
    W, H = 256 * SC, 192 * SC; pix = [bytearray([255, 255, 255] * W) for _ in range(H)]
    def fill(x, y, w, h, rgb):
        for yy in range(int(y * SC), int((y + h) * SC)):
            row = pix[yy]
            for xx in range(int(x * SC), int((x + w) * SC)): row[xx*3:xx*3+3] = bytes(rgb)
    for c in range(1, 7):
        for r in range(1, 17):
            X0, Y0 = geom.zone(c, r); fill(X0, Y0, 36, 6, (225, 225, 225)); fill(X0, Y0, 18, 6, (243, 224, 243)); fill(X0 + 26.5, Y0 + 2.5, 1, 1, (0, 0, 0))
    for k, x in geom.HOLE_X.items(): fill(x - 1, 0.5, 2, 2, (120, 120, 255)); fill(x - 1, 189.5, 2, 2, (120, 120, 255))
    for k in range(7): gx = geom.gap_x(k); fill(gx, 27, 4, 141, (240, 240, 240))
    return W, H, pix, fill
def render(code, path):
    W, H, pix, fill = canvas()
    for y, x, h, w in re.findall(r'\((\d+),(\d+)\)\((\d+),(\d+)\)', code): fill(int(x), int(y), int(w), int(h), (46, 204, 64))
    png(path, W, H, pix)
if __name__ == "__main__":
    prj, out = sys.argv[1], sys.argv[2]
    tiles = []
    for spec in sys.argv[3:]:
        fname, fk, ck, idxs = spec.split(":")
        name, codes = steps_of(prj, fname, fk, ck)
        for i in idxs.split(","):
            i = int(i); i = min(i, len(codes) - 1)
            render(codes[i], f"{out}_{fk}_{i+1}.png"); print(f"{out}_{fk}_{i+1}.png  {fk} {name} 第{i+1}/{len(codes)}步")
