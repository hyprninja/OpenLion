"""Voice packs: the grammar phrase table editor, pack checks, and installing a pack onto a card copy.

Uses a made-up grammar file and generated WAVs (no factory data in the repo)."""
import io
import json
import os
import shutil
import struct
import sys
import tempfile
import unittest
import wave
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from openlion import build, voice_grammar, voicepack  # noqa: E402

FACTORY_PHRASES = [  # (text, clip, weight), longest first like the factory table
    ("The navigation system is ready.", "navready", 700), ("meters ahead.", "f_meters", 700),
    ("[P50ms]", "50msp", 50), ("Turn", "iturn", 700), ("left", "mleft2", 700), ("300", "md300", 700),
]


def utf16(s, flag=None):
    return (b"" if flag is None else bytes([flag])) + struct.pack("<H", len(s)) + s.encode("utf-16le")


def grammar(phrases):
    """A small file laid out like Language_EnglishUK.bgl: header, grammar text, table, more text, EOF."""
    head = utf16("English_UK", 1) + utf16("VS_SIMPLE_1") + utf16("$VX_INSTRUCTION:0 $VX_DIRECTION:0 $VX_DIST")
    table = struct.pack("<HH", len(phrases), len(phrases)) + b"".join(
        utf16(t, 1) + utf16(c) + struct.pack("<I", w) for t, c, w in phrases)
    tail = utf16("VX_DIST_S_METER2", 1) + utf16("300 meters ahead") + b"EOF"
    total = sum(len(s) for s in ("English_UK", "VS_SIMPLE_1", "$VX_INSTRUCTION:0 $VX_DIRECTION:0 $VX_DIST",
                                 "VX_DIST_S_METER2", "300 meters ahead")) + sum(len(t) + len(c) for t, c, _ in phrases)
    return struct.pack("<4I", 257, 3, 0, total) + head + b"\x00\x00\x01" + table + tail


def wav(seconds=0.3, rate=16000, channels=1, tone=0):
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(channels); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(struct.pack("<h", tone) * int(seconds * rate) * channels)
    return b.getvalue()


class Grammar(unittest.TestCase):
    def test_identity_and_read(self):
        d = grammar(FACTORY_PHRASES)
        _, table, _ = voice_grammar.read_table(d)
        self.assertEqual(table, FACTORY_PHRASES)
        self.assertEqual(voice_grammar.build(d), d)

    def test_add_keeps_order_and_counts(self):
        d = grammar(FACTORY_PHRASES)
        n = voice_grammar.build(d, [("Turn left", "ol001"), ("300 meters ahead.", "ol002"),
                                    ("Turn", "dupe")])          # already there: ignored
        _, table, _ = voice_grammar.read_table(n)
        texts = [t for t, _, _ in table]
        self.assertEqual(len(table), len(FACTORY_PHRASES) + 2)
        self.assertEqual(texts, sorted(texts, key=len, reverse=True))
        self.assertIn(("Turn left", "ol001", 700), table)
        self.assertEqual(dict((t, c) for t, c, _ in table)["Turn"], "iturn")
        added = len("Turn left") + 5 + len("300 meters ahead.") + 5
        self.assertEqual(struct.unpack_from("<I", n, 12)[0], struct.unpack_from("<I", d, 12)[0] + added)
        self.assertTrue(n.endswith(b"EOF"))

    def test_bad_clip_name(self):
        with self.assertRaises(ValueError):
            voice_grammar.build(grammar(FACTORY_PHRASES), [("Turn right", "toolongname")])


class Packs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp)
        # factory card copy: voices/en_GB with grammar, speech clips and a pause clip
        self.nav = os.path.join(self.tmp, "factory", "navigation")
        v = os.path.join(self.nav, "voices", "en_GB")
        os.makedirs(v)
        os.makedirs(os.path.join(self.nav, "voices", "en_US"))
        with open(os.path.join(v, "Language_EnglishUK.bgl"), "wb") as f:
            f.write(grammar(FACTORY_PHRASES))
        for _, c, _ in FACTORY_PHRASES:
            with open(os.path.join(v, c + ".wav"), "wb") as f:
                f.write(wav(tone=1))
        with open(os.path.join(self.nav, "voices", "en_US", "iturn.wav"), "wb") as f:
            f.write(wav(tone=1))
        # pack source: new speech clips + two longer phrases
        self.clips = os.path.join(self.tmp, "clips")
        os.makedirs(self.clips)
        for c in ("navready", "f_meters", "iturn", "mleft2", "md300", "ol001", "ol002"):
            with open(os.path.join(self.clips, c + ".wav"), "wb") as f:
                f.write(wav(tone=9))
        self.phrases = os.path.join(self.tmp, "phrases.tsv")
        with open(self.phrases, "w", encoding="utf-8") as f:
            f.write("Turn left\tol001\n300 meters ahead.\tol002\n")
        self.meta = os.path.join(self.tmp, "meta.json")
        with open(self.meta, "w", encoding="utf-8") as f:
            json.dump({"id": "test-v1", "name": "Test voice", "credit": "test", "licence": "CC0"}, f)
        self.pack = os.path.join(self.tmp, "pack.zip")

    def test_make_load_install_build(self):
        p = voicepack.make(self.clips, self.phrases, self.meta, self.pack)
        self.assertEqual(len(p.clips), 7)
        rep, add, summary = voicepack.install(self.nav, p)
        self.assertEqual(set(add), {"voices/en_GB/ol001.wav", "voices/en_GB/ol002.wav"})
        self.assertIn("voices/en_GB/Language_EnglishUK.bgl", rep)
        self.assertEqual(summary["new_phrases"], 2)
        self.assertEqual(summary["factory_speech_kept"], [])
        out = os.path.join(self.tmp, "out")
        report = build.build(self.nav, out, {}, (rep, add))
        self.assertEqual(sorted(report["added"]), sorted(add))
        _, table, _ = voice_grammar.read_table(open(os.path.join(out, "voices/en_GB/Language_EnglishUK.bgl"), "rb").read())
        self.assertIn(("Turn left", "ol001", 700), table)
        # pause clip and the other language are the factory files
        for rel in ("voices/en_GB/50msp.wav", "voices/en_US/iturn.wav"):
            self.assertEqual(open(os.path.join(out, rel), "rb").read(), open(os.path.join(self.nav, rel), "rb").read())
        self.assertEqual(open(os.path.join(out, "voices/en_GB/iturn.wav"), "rb").read(), wav(tone=9))

    def test_pack_may_only_touch_en_gb(self):
        with self.assertRaises(ValueError):
            build.build(self.nav, os.path.join(self.tmp, "out"), {}, ({"voices/en_US/iturn.wav": b"x"}, {}))

    def test_damaged_clip_is_refused(self):
        voicepack.make(self.clips, self.phrases, self.meta, self.pack)
        with zipfile.ZipFile(self.pack) as z:
            items = {n: z.read(n) for n in z.namelist()}
        items["en_GB/iturn.wav"] = wav(tone=3)
        with zipfile.ZipFile(self.pack, "w") as z:
            for n, b in items.items():
                z.writestr(n, b)
        with self.assertRaisesRegex(voicepack.VoicePackError, "damaged"):
            voicepack.load(self.pack)

    def test_wrong_wav_format_is_refused(self):
        with open(os.path.join(self.clips, "iturn.wav"), "wb") as f:
            f.write(wav(rate=22050))
        with self.assertRaisesRegex(voicepack.VoicePackError, "16 kHz"):
            voicepack.make(self.clips, self.phrases, self.meta, self.pack)

    def test_phrase_needs_its_clip(self):
        with open(self.phrases, "a", encoding="utf-8") as f:
            f.write("Keep right\tol999\n")
        with self.assertRaisesRegex(voicepack.VoicePackError, "ol999"):
            voicepack.make(self.clips, self.phrases, self.meta, self.pack)

    def test_other_languages_refused(self):
        voicepack.make(self.clips, self.phrases, self.meta, self.pack)
        with zipfile.ZipFile(self.pack) as z:
            items = {n: z.read(n) for n in z.namelist()}
        meta = json.loads(items["voicepack.json"]); meta["language"] = "en_US"
        items["voicepack.json"] = json.dumps(meta).encode()
        with zipfile.ZipFile(self.pack, "w") as z:
            for n, b in items.items():
                z.writestr(n, b)
        with self.assertRaisesRegex(voicepack.VoicePackError, "UK English"):
            voicepack.load(self.pack)


if __name__ == "__main__":
    unittest.main()
