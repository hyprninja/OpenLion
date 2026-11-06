"""Voice packs: a new voice for the unit's UK English prompts (docs/26_voice_packs.md).

A pack is a zip holding only OpenLion's own files:
  voicepack.json   {"format": 1, "id", "name", "language": "en_GB", "description",
                    "credit", "licence", "clips": {clip name: sha256 of the WAV},
                    "phrases": [[text, clip name], ...]}
  en_GB/<clip>.wav 16 kHz mono 16-bit PCM

Installing it onto a card copy (no factory file is ever part of a pack):
  - each pack clip replaces the factory clip of the same name, or is added;
  - "phrases" are added to the user's own Language_EnglishUK.bgl phrase table
    (voice_grammar.build), so the unit can play longer phrases in one go;
  - every other factory file (the beeps, click and pauses, the other languages)
    stays as it is.

  python -m openlion.voicepack check <pack.zip> [<factory navigation/>]
  python -m openlion.voicepack make <clips dir> <phrases.tsv> <meta.json> <pack.zip>
  python -m openlion.voicepack sample <pack.zip> <factory navigation/> <sample.wav>
      (the listening sample the app plays before a pack is downloaded)
"""
import hashlib
import io
import json
import os
import re
import sys
import wave
import zipfile

from . import voice_grammar

FORMAT = 1
LANGUAGE = "en_GB"
GRAMMAR = "Language_EnglishUK.bgl"
CLIP_NAME = re.compile(r"^[a-z0-9_]{1,8}$")
MAX_PACK_BYTES = 200_000_000
MAX_CLIP_SECONDS = 10


class VoicePackError(ValueError):
    pass


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def check_wav(name, b):
    """Raise unless b is a short 16 kHz mono 16-bit PCM WAV."""
    try:
        with wave.open(io.BytesIO(b)) as w:
            fmt = (w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getcomptype())
            secs = w.getnframes() / max(1, w.getframerate())
    except (wave.Error, EOFError) as e:
        raise VoicePackError(f"{name}.wav is not a readable WAV file ({e})") from None
    if fmt != (16000, 1, 2, "NONE"):
        raise VoicePackError(f"{name}.wav must be 16 kHz mono 16-bit PCM, like the factory clips")
    if not 0 < secs <= MAX_CLIP_SECONDS:
        raise VoicePackError(f"{name}.wav is {secs:.1f} s long")


class Pack:
    def __init__(self, meta, clips):
        self.meta, self.clips = meta, clips      # clips: {name: wav bytes}

    @property
    def id(self):
        return self.meta["id"]

    @property
    def name(self):
        return self.meta["name"]

    @property
    def phrases(self):
        return [(t, s) for t, s in self.meta.get("phrases", [])]


def load(path):
    """Read and check a pack. Raises VoicePackError with a plain-English reason."""
    if not os.path.isfile(path):
        raise VoicePackError(f"voice pack not found: {path}")
    if os.path.getsize(path) > MAX_PACK_BYTES:
        raise VoicePackError("the voice pack is far bigger than any voice could be")
    try:
        z = zipfile.ZipFile(path)
    except zipfile.BadZipFile:
        raise VoicePackError("the voice pack is not a zip file, or the download is damaged") from None
    with z:
        try:
            meta = json.loads(z.read("voicepack.json"))
        except KeyError:
            raise VoicePackError("the voice pack has no voicepack.json") from None
        except (ValueError, UnicodeDecodeError):
            raise VoicePackError("the voice pack's voicepack.json can't be read") from None
        if meta.get("format") != FORMAT:
            raise VoicePackError(f"this voice pack needs a newer OpenLion (pack format {meta.get('format')})")
        if meta.get("language") != LANGUAGE:
            raise VoicePackError(f"only UK English voice packs are supported, not {meta.get('language')!r}")
        for k in ("id", "name"):
            if not isinstance(meta.get(k), str) or not meta[k]:
                raise VoicePackError(f"the voice pack has no {k}")
        listed = meta.get("clips")
        if not isinstance(listed, dict) or not listed:
            raise VoicePackError("the voice pack lists no clips")
        clips = {}
        for name, digest in listed.items():
            if not CLIP_NAME.match(name):
                raise VoicePackError(f"bad clip name in the voice pack: {name!r}")
            try:
                b = z.read(f"{LANGUAGE}/{name}.wav")
            except KeyError:
                raise VoicePackError(f"the voice pack is missing {name}.wav") from None
            if sha256(b) != digest:
                raise VoicePackError(f"{name}.wav in the voice pack is damaged (checksum differs)")
            check_wav(name, b)
            clips[name] = b
        phrases = meta.get("phrases", [])
        if not isinstance(phrases, list):
            raise VoicePackError("the voice pack's phrases are not a list")
        for p in phrases:
            if not (isinstance(p, list) and len(p) == 2 and all(isinstance(v, str) and v for v in p)):
                raise VoicePackError(f"bad phrase in the voice pack: {p!r}")
            if p[1] not in clips:
                raise VoicePackError(f"phrase {p[0]!r} needs {p[1]}.wav, which the pack doesn't have")
    return Pack(meta, clips)


def voices_folder(factory_nav):
    """(relative path, absolute path) of the factory card's voices/en_GB folder, in the
    card's own spelling of the folder names."""
    rel = []
    cur = factory_nav
    for part in ("voices", LANGUAGE):
        found = next((n for n in (os.listdir(cur) if os.path.isdir(cur) else [])
                      if n.lower() == part.lower() and os.path.isdir(os.path.join(cur, n))), None)
        if found is None:
            raise VoicePackError(f"the factory card copy has no {'/'.join(rel + [part])} folder")
        rel.append(found)
        cur = os.path.join(cur, found)
    return "/".join(rel), cur


def install(factory_nav, pack):
    """Files for the new card: ({rel path: bytes} replacing factory files,
    {rel path: bytes} new files, summary dict). Paths are relative to navigation/."""
    rel, folder = voices_folder(factory_nav)
    by_lower = {n.lower(): n for n in os.listdir(folder)}
    grammar_name = by_lower.get(GRAMMAR.lower())
    if grammar_name is None:
        raise VoicePackError(f"the factory card copy has no {rel}/{GRAMMAR}")
    with open(os.path.join(folder, grammar_name), "rb") as f:
        factory_grammar = f.read()
    try:
        _, factory_table, _ = voice_grammar.read_table(factory_grammar)
        grammar = voice_grammar.build(factory_grammar, pack.phrases)
        _, table, _ = voice_grammar.read_table(grammar)
    except ValueError as e:
        raise VoicePackError(f"the factory voice grammar file couldn't be read ({e})") from None
    replace, add = {}, {}
    for name, b in pack.clips.items():
        existing = by_lower.get(name + ".wav")
        if existing is not None:
            replace[f"{rel}/{existing}"] = b
        else:
            add[f"{rel}/{name}.wav"] = b
    replace[f"{rel}/{grammar_name}"] = grammar
    have = {n[:-4].lower() for n in by_lower if n.endswith(".wav")} | set(pack.clips)
    missing = sorted({s for _, s, _ in table if s.lower() not in have})
    if missing:
        raise VoicePackError(f"the voice grammar names clips nobody has: {missing[:5]}")
    factory_speech = {s.lower() for _, s, w in factory_table if w == voice_grammar.SPEECH_WEIGHT}
    summary = {"pack": pack.id, "name": pack.name, "replaced_clips": len(replace) - 1, "new_clips": len(add),
               "phrases": len(table), "new_phrases": len(table) - len(factory_table),
               "factory_speech_kept": sorted(factory_speech - set(pack.clips))}
    return replace, add, summary


def make(clips_dir, phrases_tsv, meta_json, out):
    """Build a pack from a folder of WAVs, a phrases file (text<TAB>clip per line) and
    the pack's metadata (id, name, description, credit, licence)."""
    with open(meta_json, encoding="utf-8") as f:
        meta = json.load(f)
    phrases = []
    with open(phrases_tsv, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\r\n")
            if line:
                text, clip = line.split("\t")
                phrases.append([text, clip])
    clips = {}
    for fn in sorted(os.listdir(clips_dir)):
        if fn.lower().endswith(".wav"):
            with open(os.path.join(clips_dir, fn), "rb") as f:
                clips[fn[:-4].lower()] = f.read()
    meta.update({"format": FORMAT, "language": LANGUAGE,
                 "clips": {n: sha256(b) for n, b in clips.items()}, "phrases": phrases})
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("voicepack.json", json.dumps(meta, indent=1, ensure_ascii=False))
        for n, b in clips.items():
            z.writestr(f"{LANGUAGE}/{n}.wav", b)
    return load(out)


SAMPLE_SENTENCES = (
    "The navigation system is ready.",
    "Turn left 300 meters ahead.",
    "Turn right 300 meters ahead and then immediately turn left.",
)


def split(text, table):
    """Clip names for a sentence: longest phrase first at word boundaries, as the unit
    appears to do. Words with no clip are skipped."""
    phrases = sorted({t: s for t, s, _ in table}.items(), key=lambda kv: -len(kv[0]))
    out, p = [], 0
    while p < len(text):
        if text[p] == " ":
            p += 1
            continue
        for t, s in phrases:
            if text.startswith(t, p) and (p + len(t) == len(text) or text[p + len(t)] == " "):
                out.append(s)
                p += len(t)
                break
        else:
            q = text.find(" ", p)
            p = len(text) if q < 0 else q
    return out


def sample_wav(pack, grammar, sentences=SAMPLE_SENTENCES, gap=0.6):
    """A listening sample: each sentence joined from the pack's clips the way the unit
    would, with a pause between sentences. grammar: the factory grammar file's bytes
    (its phrase table plus the pack's phrases decide which clips are used)."""
    _, table, _ = voice_grammar.read_table(voice_grammar.build(grammar, pack.phrases))
    frames = bytearray()
    for n, s in enumerate(sentences):
        if n:
            frames += b"\x00\x00" * int(gap * 16000)
        for clip in split(s, table):
            if clip not in pack.clips:
                raise VoicePackError(f"the sample needs {clip}.wav, which the pack doesn't have")
            with wave.open(io.BytesIO(pack.clips[clip])) as w:
                frames += w.readframes(w.getnframes())
    b = io.BytesIO()
    with wave.open(b, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(bytes(frames))
    return b.getvalue()


def main(argv):
    if len(argv) == 4 and argv[0] == "sample":
        pack = load(argv[1])
        _, folder = voices_folder(argv[2])
        grammar = next(n for n in os.listdir(folder) if n.lower() == GRAMMAR.lower())
        with open(os.path.join(folder, grammar), "rb") as f:
            b = sample_wav(pack, f.read())
        with open(argv[3], "wb") as f:
            f.write(b)
        print(f"{argv[3]}: {len(b) / 32000:.1f} s")
        return 0
    if len(argv) >= 2 and argv[0] == "check":
        p = load(argv[1])
        print(f"{p.name} ({p.id}): {len(p.clips)} clips, {len(p.phrases)} extra phrases")
        if len(argv) > 2:
            rep, add, s = install(argv[2], p)
            print(f"on this card: {s['replaced_clips']} clips replaced, {s['new_clips']} added, "
                  f"{s['phrases']} phrases ({s['new_phrases']} new); factory speech clips kept: "
                  f"{len(s['factory_speech_kept'])}")
        return 0
    if len(argv) == 5 and argv[0] == "make":
        p = make(*argv[1:])
        print(f"{argv[4]}: {len(p.clips)} clips, {len(p.phrases)} extra phrases")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main(sys.argv[1:]))
    except VoicePackError as e:
        print("voice pack problem:", e, file=sys.stderr)
        sys.exit(1)
