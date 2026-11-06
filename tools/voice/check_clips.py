"""Check every clip in a voice pack says what it should, using ElevenLabs speech-to-text.

  set OPENLION_VOICE_WORK=... and OPENLION_FACTORY_GRAMMAR=... (as for make_pack.py)
  python tools/voice/check_clips.py <pack.zip> <report.tsv>

What each clip should say comes from the same carriers make_pack.py used (jokes
included). A clip is flagged when the transcript is missing words, has extra ones, or
its first or last word differs: the signs of a cut in the wrong place. It can't judge
accents; listen to the flagged clips and a few others.
Transcripts are cached in the work folder (stt/), so a rerun only checks new clips.
"""
import difflib
import io
import json
import os
import re
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eleven, phrases  # noqa: E402

LETTER_CLIPS = {"A", "B", "E", "I", "M", "N", "O", "R", "S", "IP", "SS", "US"}


def expected():
    """{clip name: spoken text}, in make_pack.py's order and naming."""
    out, n_new = {}, 0
    for carrier, is_new in phrases.all_carriers():
        for spoken, key, stem in carrier:
            if stem:
                if is_new:
                    n_new += 1
                    stem = f"ol{n_new:03d}"
                out[stem] = spoken
    return out


def norm(text):
    words = []
    for w in re.sub(r"[^a-z0-9' -]", " ", text.lower().replace("-", " ")).split():
        w = w.strip("'")
        if w.isdigit():
            words += phrases.words(int(w)).replace("-", " ").split()
        elif w:
            words.append(w)
    return words


def transcribe(name, wav, cache):
    import hashlib
    path = os.path.join(cache, f"{name}_{hashlib.sha1(wav).hexdigest()[:10]}.json")   # re-checked when re-cut
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))["text"]
    nl = (chr(13) + chr(10)).encode()
    boundary = "openlion" + os.urandom(8).hex()
    b = f"--{boundary}".encode()
    body = (b + nl + b'Content-Disposition: form-data; name="model_id"' + nl + nl + b"scribe_v2" + nl +
            b + nl + b'Content-Disposition: form-data; name="language_code"' + nl + nl + b"en" + nl +
            b + nl + b'Content-Disposition: form-data; name="tag_audio_events"' + nl + nl + b"false" + nl +
            b + nl + b'Content-Disposition: form-data; name="file"; filename="a.wav"' + nl +
            b"Content-Type: audio/wav" + nl + nl + wav + nl + b + b"--" + nl)
    import urllib.request
    req = urllib.request.Request(eleven.API + "/v1/speech-to-text", data=body, method="POST",
                                 headers={"xi-api-key": eleven.key(),
                                          "Content-Type": f"multipart/form-data; boundary={boundary}"})
    import time
    import urllib.error
    for attempt in range(8):
        try:
            text = json.loads(urllib.request.urlopen(req, timeout=120).read()).get("text", "")
            break
        except urllib.error.HTTPError as e:
            if e.code == 400:
                return "(refused: " + e.read().decode(errors="replace")[:160] + ")"   # e.g. under 100 ms
            if e.code in (429, 500, 502, 503) and attempt < 7:
                time.sleep(min(60, 5 * (attempt + 1)))
                continue
            raise
    json.dump({"text": text}, open(path, "w", encoding="utf-8"))
    return text


def main(pack, report):
    want = expected()
    cache = os.path.join(eleven.VOICE_DIR, "stt")
    os.makedirs(cache, exist_ok=True)
    rows, flagged = [], 0
    with zipfile.ZipFile(pack) as z:
        names = sorted(n[6:-4] for n in z.namelist() if n.startswith("en_GB/"))
        for k, name in enumerate(names, 1):
            heard = transcribe(name, z.read(f"en_GB/{name}.wav"), cache)
            e, h = norm(want.get(name, "")), norm(heard)
            ratio = difflib.SequenceMatcher(None, e, h).ratio() if e else 0
            ends_ok = bool(e and h and e[0] == h[0] and e[-1] == h[-1])
            letter = want.get(name, "").replace(" ", "") in LETTER_CLIPS
            bad = not letter and (ratio < 0.8 or not ends_ok)
            flagged += bad
            rows.append((name, "CHECK" if bad else ("letter" if letter else "ok"), f"{ratio:.2f}",
                         want.get(name, ""), heard))
            print(f"\r{k}/{len(names)} clips, {flagged} flagged", end="", flush=True)
    print()
    with open(report, "w", encoding="utf-8") as f:
        f.write("clip\tresult\tmatch\tshould say\theard\n")
        for r in sorted(rows, key=lambda r: (r[1] != "CHECK", float(r[2]))):
            f.write("\t".join(r) + "\n")
    print(f"{flagged} clips to check -> {report}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
