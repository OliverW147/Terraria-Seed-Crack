"""Read an XNB Texture2D (LZX compressed) and dump it as PNG."""
import struct
import sys
import os
from lzx import LzxDecoder

XNB_HEADER = 14  # magic(3) + platform(1) + version(1) + flags(1) + filesize(4) + decompsize(4)


def read7(d, p):
    v = 0
    shift = 0
    while True:
        b = d[p]
        p += 1
        v |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
    return v, p


def decompress_xnb(path):
    d = open(path, "rb").read()
    if d[:3] != b"XNB":
        raise ValueError("not an XNB file")
    platform = chr(d[3])
    version = d[4]
    flags = d[5]
    filesize = struct.unpack_from("<I", d, 6)[0]
    compressed = bool(flags & 0x80)

    if not compressed:
        return d[10:]

    decomp_size = struct.unpack_from("<I", d, 10)[0]
    body = d[14:filesize]

    dec = LzxDecoder(16)
    out = bytearray()
    pos = 0
    while pos < len(body) and len(out) < decomp_size:
        if body[pos] == 0xFF:
            # extended header: 0xFF, frame_size(2), block_size(2)
            pos += 1
            frame_size = (body[pos] << 8) | body[pos + 1]
            pos += 2
            block_size = (body[pos] << 8) | body[pos + 1]
            pos += 2
        else:
            frame_size = 0x8000
            block_size = (body[pos] << 8) | body[pos + 1]
            pos += 2
        if block_size == 0 or frame_size == 0:
            break
        frame_size = min(frame_size, decomp_size - len(out))
        chunk = dec.decompress(body[pos:pos + block_size], frame_size)
        out += chunk
        pos += block_size
    return bytes(out)


def parse_texture(raw):
    q = 0
    n, q = read7(raw, q)
    readers = []
    for _ in range(n):
        ln, q = read7(raw, q)
        name = raw[q:q + ln].decode("utf-8", "replace")
        q += ln
        q += 4  # reader version
        readers.append(name)
    nshared, q = read7(raw, q)
    typeid, q = read7(raw, q)

    fmt = struct.unpack_from("<i", raw, q)[0]; q += 4
    w = struct.unpack_from("<i", raw, q)[0]; q += 4
    h = struct.unpack_from("<i", raw, q)[0]; q += 4
    mips = struct.unpack_from("<i", raw, q)[0]; q += 4
    size = struct.unpack_from("<i", raw, q)[0]; q += 4
    pixels = raw[q:q + size]
    return readers, fmt, w, h, pixels


if __name__ == "__main__":
    src = sys.argv[1]
    dst = sys.argv[2] if len(sys.argv) > 2 else "out.png"
    raw = decompress_xnb(src)
    print("decompressed %d bytes" % len(raw))
    readers, fmt, w, h, pixels = parse_texture(raw)
    print("readers:", readers)
    print("format=%d  %dx%d  pixeldata=%d" % (fmt, w, h, len(pixels)))
    if fmt != 0:
        print("WARNING: format %d is not Color(0); output may be wrong" % fmt)

    from PIL import Image
    img = Image.frombytes("RGBA", (w, h), pixels[:w * h * 4])
    img.save(dst)
    print("wrote", dst)
