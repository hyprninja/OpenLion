"""Shared pieces of the Siemens/Continental table format."""
import struct

HDR = 0x5E  # common header size of every decompressed table / map file

# Ceilings for counts read from a card's own headers. The largest factory tables hold
# well under these (mp0: ~10k parcels; rd: ~0.75 M records, ~27 MB raw).
MAX_TABLE_BYTES = 1 << 30
MAX_PARCELS = 2_000_000


class DamagedTable(ValueError):
    """A card table whose headers don't add up (truncated, corrupt or not a real table)."""


def parse_header(data: bytes) -> dict:
    """0x00 5 x char[16] stamps, 0x50 u32 dbID, 0x54 u16 fileID,
    0x56 u32 rec_cnt, 0x5A u32 byte_cnt."""
    dbid, fid, cnt, size = struct.unpack_from("<IHII", data, 0x50)
    return {"stamps": data[:0x50], "dbID": dbid, "fileID": fid, "rec_cnt": cnt, "byte_cnt": size}


def build_header(template: bytes, rec_cnt: int, byte_cnt: int) -> bytes:
    """Copy stamps/dbID/fileID from a factory header, set the two counters."""
    return template[:0x56] + struct.pack("<II", rec_cnt, byte_cnt)
