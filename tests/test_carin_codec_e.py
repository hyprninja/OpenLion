"""Codec 0x0E string code (doc 33). Synthetic bit strings only.

  python -m unittest discover -s tests -p "test_*.py"
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import carin_codec_e as ce  # noqa: E402

CODE = {**{v.decode(): k for k, v in ce.SHORT.items()}}


def encode(text, dictionary=()):
    """Reference encoder for the test: dictionary slots first, else the short code, else
    the letter / digit / punctuation groups."""
    out, i = [], 0
    while i < len(text):
        for k, e in enumerate(dictionary):
            if text.startswith(e, i):
                out.append("110100" + format(k + 1, "03b"))
                i += len(e)
                break
        else:
            c = text[i]
            if c in CODE:
                out.append(CODE[c])
            elif c == "'":
                out.append("110100111")
            elif c in "/.":
                out.append("110101" + format({"/": 4, ".": 6}[c], "03b"))
            elif c.isdigit():
                out.append("11011" + format(int(c), "04b"))
            else:
                out.append("1111" + format(ord(c) - 0x60, "05b"))
            i += 1
    return "".join(out)


class StringCode(unittest.TestCase):
    def decode(self, bits, dictionary, n):
        r = ce.Bits(b"")
        r.b, r.p = bits, 0
        return ce._text(r, [d.encode() for d in dictionary], n)

    def test_australia_known_bits(self):
        # 'aus t r ali a' + NUL, as on the factory tiles whose dictionary starts 'ali', 'aus'
        self.assertEqual(self.decode("110100010" + "0101" + "0110" + "110100001" + "000" + "0111",
                                     ["ali", "aus"], 10), b"australia\0")

    def test_round_trip_all_symbol_groups(self):
        text = "o'connell st/mt keira rd 2a.\0zebra quay\0"
        dic = ["st", "ra", "ay\0"]
        self.assertEqual(self.decode(encode(text, dic), dic, len(text)), text.encode())

    def test_unknown_code_raises(self):
        with self.assertRaises(ValueError):
            self.decode("1100" + "0" * 12, [], 1)


if __name__ == "__main__":
    unittest.main()
