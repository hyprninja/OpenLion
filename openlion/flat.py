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

try:
    from .common import DamagedTable, MAX_TABLE_BYTES
except ImportError:  # run as a script
    from common import DamagedTable, MAX_TABLE_BYTES


def unpack(data: bytes) -> bytes:
    if data[:4] != b"zip\x01":
        raise ValueError("not a zip\\x01 container")
    if len(data) < 16:
        raise DamagedTable(f"container header truncated ({len(data)} bytes)")
    bs, total, _maxc = struct.unpack_from("<III", data, 4)
    if not 0 < bs <= 1 << 20:
        raise DamagedTable(f"block size {bs} out of range")
    if total > MAX_TABLE_BYTES:
        raise DamagedTable(f"uncompressed size {total} too large")
    n = -(-total // bs)
    first = 16 + 4 * (n + 1)
    if first > len(data):
        raise DamagedTable(f"offset list ({n + 1} entries) runs past the end of the file")
    offs = struct.unpack_from(f"<{n + 1}I", data, 16)
    if offs[-1] != len(data):
        raise DamagedTable(f"end offset {offs[-1]} != file size {len(data)}")
    if (n and offs[0] < first) or any(offs[i] > offs[i + 1] for i in range(n)):
        raise DamagedTable("block offsets out of order")
    out = bytearray()
    for i in range(n):
        try:
            blk = zlib.decompress(data[offs[i]:offs[i + 1]])
        except zlib.error as e:
            raise DamagedTable(f"block {i}: {e}") from None
        want = min(bs, total - i * bs)
        if len(blk) != want:
            raise DamagedTable(f"block {i}: got {len(blk)} want {want}")
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
    head = b"zip\x01" + struct.pack("<III", block_size, len(raw), max(map(len, blocks), default=0))
    return head + struct.pack(f"<{len(offs)}I", *offs) + b"".join(blocks)


if __name__ == "__main__":
    src, dst = sys.argv[1], sys.argv[2]
    raw = unpack(open(src, "rb").read())
    open(dst, "wb").write(raw)
    print(f"{src}: {len(raw)} bytes")
