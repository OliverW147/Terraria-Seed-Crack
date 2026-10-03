"""LZX decompressor for XNB files (MS-LZX / CAB variant, no delta frames)."""
import struct

MIN_MATCH = 2
NUM_CHARS = 256
BLOCKTYPE_VERBATIM = 1
BLOCKTYPE_ALIGNED = 2
BLOCKTYPE_UNCOMPRESSED = 3
PRETREE_NUM_ELEMENTS = 20
ALIGNED_NUM_ELEMENTS = 8
NUM_SECONDARY_LENGTHS = 249

position_base = []
extra_bits = []


def _init_tables():
    global position_base, extra_bits
    extra_bits = []
    for i in range(0, 52):
        if i < 4:
            extra_bits.append(0)
        elif i < 36:
            extra_bits.append((i // 2) - 1)
        else:
            extra_bits.append(17)
    position_base = []
    base = 0
    for i in range(52):
        position_base.append(base)
        base += 1 << extra_bits[i]


_init_tables()


class BitReader:
    """LZX reads 16-bit little-endian words, MSB-first within the word."""

    def __init__(self, data):
        self.data = data
        self.pos = 0
        self.bitbuf = 0
        self.bitcount = 0

    def ensure(self, n):
        while self.bitcount < n:
            if self.pos + 1 < len(self.data):
                word = self.data[self.pos] | (self.data[self.pos + 1] << 8)
            elif self.pos < len(self.data):
                word = self.data[self.pos]
            else:
                word = 0
            self.pos += 2
            self.bitbuf = ((self.bitbuf << 16) | word) & 0xFFFFFFFFFFFF
            self.bitcount += 16

    def peek(self, n):
        if n == 0:
            return 0
        self.ensure(n)
        return (self.bitbuf >> (self.bitcount - n)) & ((1 << n) - 1)

    def remove(self, n):
        self.bitcount -= n
        self.bitbuf &= (1 << self.bitcount) - 1

    def read(self, n):
        if n == 0:
            return 0
        v = self.peek(n)
        self.remove(n)
        return v

    def align(self):
        """Skip to the next 16-bit boundary."""
        n = self.bitcount % 16
        if n:
            self.remove(n)

    def read_bytes_aligned(self, count):
        self.align()
        # after aligning, bitbuf holds whole words we must consume first
        out = bytearray()
        while self.bitcount >= 8 and count > 0:
            self.bitcount -= 8
            out.append((self.bitbuf >> self.bitcount) & 0xFF)
            self.bitbuf &= (1 << self.bitcount) - 1
            count -= 1
        if count:
            out += self.data[self.pos:self.pos + count]
            self.pos += count
        return bytes(out)


class HuffTable:
    """Canonical Huffman decoder built from a code-length table."""

    def __init__(self, lengths, maxbits):
        self.lengths = lengths
        self.maxbits = maxbits
        self.lookup = {}
        counts = [0] * (maxbits + 1)
        for L in lengths:
            if L:
                counts[L] += 1
        code = 0
        next_code = [0] * (maxbits + 2)
        for bits in range(1, maxbits + 1):
            code = (code + counts[bits - 1]) << 1
            next_code[bits] = code
        for sym, L in enumerate(lengths):
            if L:
                self.lookup[(L, next_code[L])] = sym
                next_code[L] += 1

    def decode(self, br):
        code = 0
        for L in range(1, self.maxbits + 1):
            code = (code << 1) | br.read(1)
            s = self.lookup.get((L, code))
            if s is not None:
                return s
        raise ValueError("invalid huffman code")


def read_lengths(br, lengths, first, last):
    """Read a pretree, then delta-decode code lengths in [first, last)."""
    pre_lens = [br.read(4) for _ in range(PRETREE_NUM_ELEMENTS)]
    pretree = HuffTable(pre_lens, 16)

    i = first
    while i < last:
        sym = pretree.decode(br)
        if sym == 17:
            run = br.read(4) + 4
            for _ in range(run):
                if i < last:
                    lengths[i] = 0
                    i += 1
        elif sym == 18:
            run = br.read(5) + 20
            for _ in range(run):
                if i < last:
                    lengths[i] = 0
                    i += 1
        elif sym == 19:
            run = br.read(1) + 4
            sym2 = pretree.decode(br)
            value = (lengths[i] - sym2) % 17
            for _ in range(run):
                if i < last:
                    lengths[i] = value
                    i += 1
        else:
            lengths[i] = (lengths[i] - sym) % 17
            i += 1
    return lengths


class LzxDecoder:
    def __init__(self, window_bits=16):
        self.window_size = 1 << window_bits
        self.window = bytearray(self.window_size)
        self.window_posn = 0
        self.R0 = self.R1 = self.R2 = 1

        # position slots for the window size
        posn_slots = {15: 30, 16: 32, 17: 34, 18: 36, 19: 38, 20: 42, 21: 50}[window_bits]
        self.main_elements = NUM_CHARS + (posn_slots << 3)

        self.MAINTREE_len = [0] * (self.main_elements + 1)
        self.LENGTH_len = [0] * (NUM_SECONDARY_LENGTHS + 1)
        self.header_read = False
        self.block_remaining = 0
        self.block_type = 0
        self.maintree = None
        self.lengthtree = None
        self.alignedtree = None

    def decompress(self, data, out_size):
        br = BitReader(data)
        out = bytearray()

        if not self.header_read:
            if br.read(1):
                # e8 translation header, skip the 32-bit size
                br.read(16)
                br.read(16)
            self.header_read = True

        while len(out) < out_size:
            if self.block_remaining == 0:
                self.block_type = br.read(3)
                hi = br.read(8)
                mid = br.read(8)
                lo = br.read(8)
                self.block_remaining = (hi << 16) | (mid << 8) | lo

                if self.block_type == BLOCKTYPE_ALIGNED:
                    aligned_len = [br.read(3) for _ in range(ALIGNED_NUM_ELEMENTS)]
                    self.alignedtree = HuffTable(aligned_len, 7)

                if self.block_type in (BLOCKTYPE_VERBATIM, BLOCKTYPE_ALIGNED):
                    read_lengths(br, self.MAINTREE_len, 0, NUM_CHARS)
                    read_lengths(br, self.MAINTREE_len, NUM_CHARS, self.main_elements)
                    self.maintree = HuffTable(self.MAINTREE_len[:self.main_elements], 16)
                    read_lengths(br, self.LENGTH_len, 0, NUM_SECONDARY_LENGTHS)
                    self.lengthtree = HuffTable(self.LENGTH_len[:NUM_SECONDARY_LENGTHS], 16)
                elif self.block_type == BLOCKTYPE_UNCOMPRESSED:
                    br.align()
                    raw = br.read_bytes_aligned(12)
                    self.R0 = struct.unpack_from("<I", raw, 0)[0]
                    self.R1 = struct.unpack_from("<I", raw, 4)[0]
                    self.R2 = struct.unpack_from("<I", raw, 8)[0]
                else:
                    raise ValueError("bad block type %d" % self.block_type)

            todo = min(self.block_remaining, out_size - len(out))

            if self.block_type == BLOCKTYPE_UNCOMPRESSED:
                chunk = br.read_bytes_aligned(todo)
                out += chunk
                for b in chunk:
                    self.window[self.window_posn] = b
                    self.window_posn = (self.window_posn + 1) % self.window_size
                self.block_remaining -= todo
                continue

            produced = 0
            while produced < todo:
                sym = self.maintree.decode(br)
                if sym < NUM_CHARS:
                    out.append(sym)
                    self.window[self.window_posn] = sym
                    self.window_posn = (self.window_posn + 1) % self.window_size
                    produced += 1
                    continue

                sym -= NUM_CHARS
                match_len = sym & 7
                if match_len == 7:
                    match_len += self.lengthtree.decode(br)
                match_len += MIN_MATCH

                slot = sym >> 3
                if slot == 0:
                    match_off = self.R0
                elif slot == 1:
                    match_off = self.R1
                    self.R1 = self.R0
                    self.R0 = match_off
                elif slot == 2:
                    match_off = self.R2
                    self.R2 = self.R0
                    self.R0 = match_off
                else:
                    eb = extra_bits[slot]
                    if self.block_type == BLOCKTYPE_ALIGNED and eb >= 3:
                        verbatim = br.read(eb - 3) << 3
                        aligned = self.alignedtree.decode(br)
                        match_off = position_base[slot] - 2 + verbatim + aligned
                    else:
                        match_off = position_base[slot] - 2 + br.read(eb)
                    self.R2 = self.R1
                    self.R1 = self.R0
                    self.R0 = match_off

                src = (self.window_posn - match_off) % self.window_size
                for _ in range(match_len):
                    b = self.window[src]
                    out.append(b)
                    self.window[self.window_posn] = b
                    self.window_posn = (self.window_posn + 1) % self.window_size
                    src = (src + 1) % self.window_size
                produced += match_len

            self.block_remaining -= produced

        return bytes(out[:out_size])
