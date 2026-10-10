"""xPeLabTFT socket 传输层 codec (SM4-ECB 公开算法 + 8 字节帧头)。

帧结构 (BufferTransfer::makePacket; 设备 172.16.10.67:20216, 20603 是独立调试网口):
    [0:2]  magic 0xE0E1        (小端上线 = E1 E0)
    [2:4]  type/flags          (2 字节; 含义待真帧对齐)
    [4:8]  payload 长度        (4 字节; 端序默认小端, 待真帧确认)
    [8: ]  payload
        控制/JSON 消息 (IDE_STEP / Quant / HighVoltage …): SM4-ECB( 0xFF填充(JSON) )      —— 没有 MD5 头
        文件传输 (.elab / tar, encryptFile):              MD5(明文)[16] ‖ SM4-ECB( 0xFF填充(明文) )
填充: 长度按 PKCS7 规则 (补到 16 整数倍, 已对齐补满一整块), 但填充字节是 0xFF。

密钥不写进源码: 环境变量 XPE_SM4_KEY=<32 位 hex>, 或同目录 xpe_key.hex (一行 32 位 hex)。
自检: `python3 xpe_codec.py [真帧hex] [--file]` → SM4 官方向量 + (有密钥时) 往返测试 + 解出该帧。
      解密原文里 `}` 后若是 10 10… 而不是 FF FF…, 把 PAD 改成 "pkcs7"; 长度对不上调 LEN_ENDIAN。
"""
import os, struct, json, hashlib

def _load_key():
    h = os.environ.get("XPE_SM4_KEY", "").strip()
    f = os.path.join(os.path.dirname(os.path.abspath(__file__)), "xpe_key.hex")
    if not h and os.path.exists(f): h = open(f).read().strip()
    k = bytes.fromhex(h.replace(" ", "")) if h else b""
    assert len(k) in (0, 16), f"密钥应为 16 字节, 实际 {len(k)}"
    return k

KEY = _load_key()
HOST, PORT = "172.16.10.67", 20216
MAGIC = 0xE0E1
LEN_ENDIAN = "<"                 # 长度字段端序: "<"=小端(默认)  ">"=大端  (一条真帧确认)
PAD = "ff"                       # "ff"(默认, 二进制证实) | "pkcs7" | "zero"

# ───────────────────────── SM4 (公开国标算法, GB/T 32907) ─────────────────────────
SBOX = bytes([
0xd6,0x90,0xe9,0xfe,0xcc,0xe1,0x3d,0xb7,0x16,0xb6,0x14,0xc2,0x28,0xfb,0x2c,0x05,
0x2b,0x67,0x9a,0x76,0x2a,0xbe,0x04,0xc3,0xaa,0x44,0x13,0x26,0x49,0x86,0x06,0x99,
0x9c,0x42,0x50,0xf4,0x91,0xef,0x98,0x7a,0x33,0x54,0x0b,0x43,0xed,0xcf,0xac,0x62,
0xe4,0xb3,0x1c,0xa9,0xc9,0x08,0xe8,0x95,0x80,0xdf,0x94,0xfa,0x75,0x8f,0x3f,0xa6,
0x47,0x07,0xa7,0xfc,0xf3,0x73,0x17,0xba,0x83,0x59,0x3c,0x19,0xe6,0x85,0x4f,0xa8,
0x68,0x6b,0x81,0xb2,0x71,0x64,0xda,0x8b,0xf8,0xeb,0x0f,0x4b,0x70,0x56,0x9d,0x35,
0x1e,0x24,0x0e,0x5e,0x63,0x58,0xd1,0xa2,0x25,0x22,0x7c,0x3b,0x01,0x21,0x78,0x87,
0xd4,0x00,0x46,0x57,0x9f,0xd3,0x27,0x52,0x4c,0x36,0x02,0xe7,0xa0,0xc4,0xc8,0x9e,
0xea,0xbf,0x8a,0xd2,0x40,0xc7,0x38,0xb5,0xa3,0xf7,0xf2,0xce,0xf9,0x61,0x15,0xa1,
0xe0,0xae,0x5d,0xa4,0x9b,0x34,0x1a,0x55,0xad,0x93,0x32,0x30,0xf5,0x8c,0xb1,0xe3,
0x1d,0xf6,0xe2,0x2e,0x82,0x66,0xca,0x60,0xc0,0x29,0x23,0xab,0x0d,0x53,0x4e,0x6f,
0xd5,0xdb,0x37,0x45,0xde,0xfd,0x8e,0x2f,0x03,0xff,0x6a,0x72,0x6d,0x6c,0x5b,0x51,
0x8d,0x1b,0xaf,0x92,0xbb,0xdd,0xbc,0x7f,0x11,0xd9,0x5c,0x41,0x1f,0x10,0x5a,0xd8,
0x0a,0xc1,0x31,0x88,0xa5,0xcd,0x7b,0xbd,0x2d,0x74,0xd0,0x12,0xb8,0xe5,0xb4,0xb0,
0x89,0x69,0x97,0x4a,0x0c,0x96,0x77,0x7e,0x65,0xb9,0xf1,0x09,0xc5,0x6e,0xc6,0x84,
0x18,0xf0,0x7d,0xec,0x3a,0xdc,0x4d,0x20,0x79,0xee,0x5f,0x3e,0xd7,0xcb,0x39,0x48,
])
FK = [0xa3b1bac6, 0x56aa3350, 0x677d9197, 0xb27022dc]
CK = [0x00070e15,0x1c232a31,0x383f464d,0x545b6269,0x70777e85,0x8c939aa1,0xa8afb6bd,0xc4cbd2d9,
      0xe0e7eef5,0xfc030a11,0x181f262d,0x343b4249,0x50575e65,0x6c737a81,0x888f969d,0xa4abb2b9,
      0xc0c7ced5,0xdce3eaf1,0xf8ff060d,0x141b2229,0x30373e45,0x4c535a61,0x686f767d,0x848b9299,
      0xa0a7aeb5,0xbcc3cad1,0xd8dfe6ed,0xf4fb0209,0x10171e25,0x2c333a41,0x484f565d,0x646b7279]

def _rotl(x, n): return ((x << n) | (x >> (32 - n))) & 0xffffffff
def _tau(a): return (SBOX[(a >> 24) & 0xff] << 24) | (SBOX[(a >> 16) & 0xff] << 16) | (SBOX[(a >> 8) & 0xff] << 8) | SBOX[a & 0xff]
def _T(x):  b = _tau(x); return b ^ _rotl(b, 2) ^ _rotl(b, 10) ^ _rotl(b, 18) ^ _rotl(b, 24)
def _Tp(x): b = _tau(x); return b ^ _rotl(b, 13) ^ _rotl(b, 23)

def _round_keys(key16):
    MK = struct.unpack(">4I", key16)
    K = [MK[i] ^ FK[i] for i in range(4)]
    rk = []
    for i in range(32):
        K.append(K[i] ^ _Tp(K[i+1] ^ K[i+2] ^ K[i+3] ^ CK[i])); rk.append(K[i+4])
    return rk

def _crypt_block(b16, rk):
    X = list(struct.unpack(">4I", b16))
    for i in range(32):
        X.append(X[i] ^ _T(X[i+1] ^ X[i+2] ^ X[i+3] ^ rk[i]))
    return struct.pack(">4I", X[35], X[34], X[33], X[32])

def sm4_ecb_decrypt(data, key16):
    rk = _round_keys(key16)[::-1]
    return b"".join(_crypt_block(data[i:i+16], rk) for i in range(0, len(data), 16))

def sm4_ecb_encrypt(data, key16):
    rk = _round_keys(key16)
    return b"".join(_crypt_block(data[i:i+16], rk) for i in range(0, len(data), 16))

def _pad(b):
    n = 16 - (len(b) % 16)                       # 1..16, 对齐也补满一整块
    return b + bytes([{"ff": 0xFF, "pkcs7": n, "zero": 0}[PAD]]) * n

def _unpad(b):
    if PAD == "pkcs7":
        n = b[-1]; return b[:-n] if 1 <= n <= 16 else b
    return b.rstrip(b"\xff" if PAD == "ff" else b"\x00")

# ───────────────────────── 帧编解码 ─────────────────────────
def decode_frame(frame: bytes, file=False):
    """原始帧 → (type_flags, 内容)。file=False: 控制消息 (无 MD5, 返回 dict); file=True: 文件传输 (校验 MD5, 返回 bytes)。"""
    assert len(frame) >= 8, "帧太短"
    magic, tflags, ln = struct.unpack(LEN_ENDIAN + "HHI", frame[:8])
    assert magic == MAGIC, f"magic 不符: {magic:#06x}"
    body = frame[8:8 + ln]
    assert len(body) == ln, f"payload 不完整 {len(body)}/{ln} (或 LEN_ENDIAN 不对)"
    assert KEY, "密钥未设置 (XPE_SM4_KEY 或 xpe_key.hex)"
    if not file:
        assert ln % 16 == 0, f"控制消息长度应为 16 的倍数: {ln}"
        return tflags, json.loads(_unpad(sm4_ecb_decrypt(body, KEY)).decode("utf-8"))
    md5, ct = body[:16], body[16:]
    plain = _unpad(sm4_ecb_decrypt(ct, KEY))
    assert hashlib.md5(plain).digest() == md5, "MD5 校验失败 (可能 PAD/端序/密钥不对)"
    return tflags, plain

def encode_frame(obj, type_flags: int = 0, file=False) -> bytes:
    """控制消息: obj=dict → 帧 (无 MD5)。文件传输: obj=bytes, file=True → 帧 (MD5 头)。"""
    assert KEY, "密钥未设置 (XPE_SM4_KEY 或 xpe_key.hex)"
    plain = obj if file else json.dumps(obj, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    body = sm4_ecb_encrypt(_pad(plain), KEY)
    if file: body = hashlib.md5(plain).digest() + body
    return struct.pack(LEN_ENDIAN + "HHI", MAGIC, type_flags, len(body)) + body

if __name__ == "__main__":
    import sys
    # SM4 官方测试向量: 明文=密钥=0123456789abcdeffedcba9876543210 → 密文 681edf34d206965e86b3e94f536e4246
    tv = bytes.fromhex("0123456789abcdeffedcba9876543210")
    print("SM4 自检:", "通过" if sm4_ecb_encrypt(tv, tv).hex() == "681edf34d206965e86b3e94f536e4246" else "失败")
    if not KEY: print("未设置密钥 (XPE_SM4_KEY / xpe_key.hex) → 跳过往返自检"); sys.exit()
    m = {"cmd": "IDE_STEP", "x": [1, 2, 3]}
    ok = decode_frame(encode_frame(m, 1))[1] == m and decode_frame(encode_frame(b"elab", 2, file=True), file=True)[1] == b"elab"
    print("往返自检 (控制 + 文件):", "通过" if ok else "失败")
    args = [a for a in sys.argv[1:] if a != "--file"]
    if args:
        raw = bytes.fromhex(args[0].replace(" ", ""))
        print("解密原文 (看 } 后是 ff 还是 10):", sm4_ecb_decrypt(raw[8 + 16 * ("--file" in sys.argv):], KEY).hex(" "))
        tf, msg = decode_frame(raw, file="--file" in sys.argv)
        print(f"type/flags={tf:#06x}")
        print(json.dumps(msg, ensure_ascii=False, indent=2) if isinstance(msg, dict) else msg[:200])
