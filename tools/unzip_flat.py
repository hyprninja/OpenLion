"""Decompress Siemens/Continental 'zip\\x01' FLAT_COMPRESSED map tables.

Container layout (all little-endian):
  0x00  4s   magic  b'zip\\x01'
  0x04  u32  block size (uncompressed), 0xC00 = 3072
  0x08  u32  total uncompressed size
  0x0C  u32  largest compressed block
  0x10  u32[n+1]  file offsets of each block, last = end of file
  n = ceil(uncompressed / block size); each block is a zlib stream.

Usage: python -I unzip_flat.py <in_file> <out_file>
"""
import struct
import sys
import zlib


def unpack(data: bytes) -> bytes:
    if data[:4] != b"zip\x01":
        raise ValueError("not a zip\\x01 container")
    bs, total, _maxc = struct.unpack_from("<III", data, 4)
    n = -(-total // bs)
    offs = struct.unpack_from(f"<{n + 1}I", data, 16)
    if offs[-1] != len(data):
        raise ValueError(f"end offset {offs[-1]} != file size {len(data)}")
    out = bytearray()
    for i in range(n):
        blk = zlib.decompress(data[offs[i]:offs[i + 1]])
        want = min(bs, total - i * bs)
        if len(blk) != want:
            raise ValueError(f"block {i}: got {len(blk)} want {want}")
        out += blk
    return bytes(out)


def pack(raw: bytes, block_size: int = 0xC00, level: int = 9) -> bytes:
    """Inverse of unpack(). zlib level 9 reproduces the factory files."""
    blocks = [zlib.compress(raw[i:i + block_size], level)
              for i in range(0, len(raw), block_size)]
    pos = 16 + 4 * (len(blocks) + 1)
    offs = []
    for b in blocks:
        offs.append(pos)
        pos += len(b)
    offs.append(pos)
    head = b"zip\x01" + struct.pack("<III", block_size, len(raw), max(map(len, blocks)))
    return head + struct.pack(f"<{len(offs)}I", *offs) + b"".join(blocks)


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    raw = unpack(open(src, "rb").read())
    open(dst, "wb").write(raw)
    print(f"{src}: {len(raw)} bytes")
