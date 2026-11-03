"""Pure-Python reader for MaxMind DB (mmdb) files, per the MaxMind DB File Format spec 2.0.

Standard library only. Used offline for country/city lookups; never opens a network connection.
"""
import datetime as dt
import ipaddress
import mmap
import struct

_MARKER = b"\xab\xcd\xefMaxMind.com"
_MARKER_SEARCH_BYTES = 128 * 1024
_MAX_DEPTH = 32


class MmdbReader:
    def __init__(self, path):
        self._fh = open(path, "rb")
        self._buf = None
        try:
            try:
                self._buf = mmap.mmap(self._fh.fileno(), 0, access=mmap.ACCESS_READ)
            except ValueError as exc:  # empty file
                raise ValueError("invalid mmdb file: empty") from exc
            try:
                self._load_metadata()
            except (IndexError, struct.error) as exc:
                raise ValueError("invalid mmdb file: truncated") from exc
        except BaseException:
            self.close()
            raise

    def _load_metadata(self):
        buf = self._buf
        search_from = max(0, len(buf) - _MARKER_SEARCH_BYTES)
        pos = buf.rfind(_MARKER, search_from)
        if pos < 0:
            raise ValueError("invalid mmdb file: metadata marker not found")
        meta_start = pos + len(_MARKER)
        meta, _ = self._decode(meta_start, meta_start, 0)
        if not isinstance(meta, dict):
            raise ValueError("invalid mmdb file: metadata is not a map")
        try:
            self.node_count = int(meta["node_count"])
            self.record_size = int(meta["record_size"])
            self.ip_version = int(meta["ip_version"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid mmdb file: incomplete metadata") from exc
        if self.record_size not in (24, 28, 32):
            raise ValueError("invalid mmdb file: unsupported record size")
        if self.ip_version not in (4, 6):
            raise ValueError("invalid mmdb file: bad ip version")
        self.metadata = meta
        self._node_bytes = self.record_size // 4
        self._tree_size = self._node_bytes * self.node_count
        self._data_start = self._tree_size + 16
        if self._data_start > pos:
            raise ValueError("invalid mmdb file: search tree larger than file")
        self.db_month = "unknown"
        epoch = meta.get("build_epoch")
        if isinstance(epoch, int):
            try:
                self.db_month = dt.datetime.fromtimestamp(epoch, dt.timezone.utc).strftime("%Y-%m")
            except (OverflowError, OSError, ValueError):
                pass
        self._ipv4_start = None

    def close(self):
        if self._buf is not None:
            self._buf.close()
            self._buf = None
        if self._fh is not None:
            self._fh.close()
            self._fh = None

    def lookup(self, ip_str):
        """Return the data record (dict) for the address, or None when it is not in the database."""
        try:
            return self._lookup(ip_str)
        except (IndexError, struct.error) as exc:
            raise ValueError("invalid mmdb file: truncated") from exc

    def _lookup(self, ip_str):
        ip = ipaddress.ip_address(ip_str)
        if ip.version == 6 and ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        if ip.version == 6 and self.ip_version == 4:
            return None
        packed = ip.packed
        bit_count = len(packed) * 8
        node = 0
        if ip.version == 4 and self.ip_version == 6:
            node = self._ipv4_start_node()
        i = 0
        while i < bit_count and node < self.node_count:
            bit = (packed[i >> 3] >> (7 - (i & 7))) & 1
            node = self._record(node, bit)
            i += 1
        if node == self.node_count:
            return None
        if node < self.node_count:
            raise ValueError("invalid mmdb file: search tree ended inside a node")
        offset = self._tree_size + (node - self.node_count)
        if offset >= len(self._buf):
            raise ValueError("invalid mmdb file: data pointer outside file")
        value, _ = self._decode(offset, self._data_start, 0)
        return value

    def _ipv4_start_node(self):
        if self._ipv4_start is None:
            node = 0
            for _ in range(96):
                if node >= self.node_count:
                    break
                node = self._record(node, 0)
            self._ipv4_start = node
        return self._ipv4_start

    def _record(self, node, side):
        base = node * self._node_bytes
        buf = self._buf
        size = self.record_size
        if base + self._node_bytes > self._tree_size:
            raise ValueError("invalid mmdb file: node outside search tree")
        if size == 24:
            off = base + 3 * side
            return int.from_bytes(buf[off:off + 3], "big")
        if size == 32:
            off = base + 4 * side
            return int.from_bytes(buf[off:off + 4], "big")
        chunk = buf[base:base + 7]
        if side == 0:
            return ((chunk[3] >> 4) << 24) | int.from_bytes(chunk[0:3], "big")
        return ((chunk[3] & 0x0F) << 24) | int.from_bytes(chunk[4:7], "big")

    def _decode(self, offset, base, depth):
        """Decode one field at offset. Pointers are relative to base. Returns (value, next_offset)."""
        if depth > _MAX_DEPTH:
            raise ValueError("invalid mmdb file: nesting too deep")
        buf = self._buf
        end = len(buf)
        if offset >= end:
            raise ValueError("invalid mmdb file: read past end")
        ctrl = buf[offset]
        offset += 1
        ftype = ctrl >> 5
        if ftype == 1:
            ss = (ctrl >> 3) & 3
            vvv = ctrl & 7
            if ss == 3:
                nbytes = 4
                ptr = int.from_bytes(buf[offset:offset + 4], "big")
            else:
                nbytes = ss + 1
                raw = int.from_bytes(buf[offset:offset + nbytes], "big")
                ptr = (vvv << (8 * nbytes)) + raw + (0, 2048, 526336)[ss]
            if offset + nbytes > end:
                raise ValueError("invalid mmdb file: truncated pointer")
            value, _ = self._decode(base + ptr, base, depth + 1)
            return value, offset + nbytes
        if ftype == 0:
            if offset >= end:
                raise ValueError("invalid mmdb file: truncated type")
            ftype = buf[offset] + 7
            offset += 1
        size = ctrl & 31
        if size == 29:
            size = 29 + buf[offset]
            offset += 1
        elif size == 30:
            size = 285 + int.from_bytes(buf[offset:offset + 2], "big")
            offset += 2
        elif size == 31:
            size = 65821 + int.from_bytes(buf[offset:offset + 3], "big")
            offset += 3
        if ftype == 7:
            result = {}
            for _ in range(size):
                key, offset = self._decode(offset, base, depth + 1)
                if not isinstance(key, str):
                    raise ValueError("invalid mmdb file: map key is not a string")
                result[key], offset = self._decode(offset, base, depth + 1)
            return result, offset
        if ftype == 11:
            items = []
            for _ in range(size):
                item, offset = self._decode(offset, base, depth + 1)
                items.append(item)
            return items, offset
        if ftype == 14:
            return bool(size), offset
        if offset + size > end:
            raise ValueError("invalid mmdb file: field past end")
        raw = bytes(buf[offset:offset + size])
        offset += size
        if ftype == 2:
            return raw.decode("utf-8"), offset
        if ftype == 3:
            if size != 8:
                raise ValueError("invalid mmdb file: bad double size")
            return struct.unpack(">d", raw)[0], offset
        if ftype == 15:
            if size != 4:
                raise ValueError("invalid mmdb file: bad float size")
            return struct.unpack(">f", raw)[0], offset
        if ftype == 4:
            return raw, offset
        if ftype in (5, 6, 9, 10):
            return int.from_bytes(raw, "big"), offset
        if ftype == 8:
            return int.from_bytes(raw, "big", signed=(size == 4)), offset
        raise ValueError("invalid mmdb file: unknown field type")
