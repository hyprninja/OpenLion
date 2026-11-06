"""Make a voice pack: re-voiced clips plus longer phrases (docs/26_voice_packs.md).

  set OPENLION_VOICE_WORK=<folder outside the repo>   (API key, voice.json, cache)
  set OPENLION_FACTORY_GRAMMAR=<factory navigation/voices/en_GB/Language_EnglishUK.bgl>
  python tools/voice/make_pack.py <meta.json> <out pack.zip>

Every carrier sentence (phrases.py) is spoken once by ElevenLabs (cached in the work
folder, so reruns cost nothing), brought to the same loudness, then cut into clips at
the quietest point between words. The pack holds only these clips; the factory beeps,
click and pauses stay on the card.
"""
import array, csv, hashlib, json, os, re, subprocess, sys, tempfile, wave
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import eleven, phrases  # noqa: E402
from openlion import voicepack  # noqa: E402

SR = 16000
CACHE = os.path.join(eleven.VOICE_DIR, 'cache')
TARGET_LUFS = -18.0
FADE = int(0.004 * SR)


def read(path):
    with wave.open(path) as w:
        return array.array('h', w.readframes(w.getnframes()))


def write(path, x):
    with wave.open(path, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR); w.writeframes(x.tobytes())


def lufs(path):
    err = subprocess.run(['ffmpeg', '-v', 'info', '-i', path, '-af', 'ebur128', '-f', 'null', '-'],
                         capture_output=True, text=True).stderr
    m = re.findall(r'I:\s+(-?[\d.]+) LUFS', err)
    return float(m[-1]) if m else None


def voiced(text):
    """Cached (samples, words) for a sentence, at TARGET_LUFS."""
    os.makedirs(CACHE, exist_ok=True)
    # A sentence that came out wrong (accent drift, a slip) can be re-voiced on its own:
    # style.json "rerolls": {"sentence": seed}. The default seed keeps the old cache.
    seed = eleven.STYLE.get('rerolls', {}).get(text, 7)
    h = hashlib.sha1((text + json.dumps(eleven.VOICE_SETTINGS) + eleven.TWEAK +
                      ('' if seed == 7 else f'|seed={seed}')).encode()).hexdigest()[:16]
    wav, meta = os.path.join(CACHE, h + '.wav'), os.path.join(CACHE, h + '.json')
    if not os.path.exists(meta):
        raw = os.path.join(CACHE, h + '.raw.wav')
        words = eleven.say_timed(raw, text, seed)
        level = lufs(raw)
        gain = TARGET_LUFS - level if level is not None and level > -70 else 0
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', raw, '-af', f'volume={gain:.2f}dB,alimiter=limit=0.95',
                        '-c:a', 'pcm_s16le', wav], check=True)
        os.remove(raw)
        json.dump({'text': text, 'words': words, 'gain_db': round(gain, 2)}, open(meta, 'w'), indent=1)
    m = json.load(open(meta))
    if eleven.STYLE.get('align') and 'aligned' not in m:
        m['aligned'] = eleven.align(wav, text)      # None if the words didn't line up one to one
        json.dump(m, open(meta, 'w'), indent=1)
    return read(wav), (m.get('aligned') or m['words'])


def quietest(x, a, b, win=int(0.01 * SR)):
    a, b = max(0, a), min(len(x) - win, b)
    if b <= a:
        return max(0, min(len(x), (a + b) // 2))
    best, at = None, a
    for i in range(a, b, int(0.002 * SR)):
        e = sum(v * v for v in x[i:i + win])
        if best is None or e < best:
            best, at = e, i + win // 2
    return at


def cut(x, words, i, j):
    """Samples for words i..j, split at the quiet points around them."""
    t = lambda s: int(s * SR)
    # Aligned timings can still be ~40 ms off: look for the quiet spot a little wider.
    slack = int(eleven.STYLE.get('cut_slack', 0.04 if eleven.STYLE.get('align') else 0) * SR)
    start = 0 if i == 0 else quietest(x, t(words[i - 1][2]) - slack, t(words[i][1]) + slack)
    end = len(x) if j == len(words) - 1 else quietest(x, t(words[j][2]) - slack, t(words[j + 1][1]) + slack)
    # Sentence start/end: trim silence to ~10 ms before and ~20 ms after the speech.
    seg = x[start:end]
    peak = max((abs(v) for v in seg), default=0) or 1
    loud = [k for k, v in enumerate(seg) if abs(v) > peak * 0.02]
    s0 = start
    if loud and j == len(words) - 1:
        end = min(end, s0 + loud[-1] + int(0.02 * SR))
    elif loud:   # mid-sentence: keep at most 80 ms of pause after the words (commas)
        end = min(end, s0 + loud[-1] + int(0.08 * SR))
    if loud and i == 0:
        start = s0 + max(0, loud[0] - int(0.01 * SR))
    c = array.array('h', x[start:end])
    for k in range(min(FADE, len(c) // 2)):
        c[k] = int(c[k] * k / FADE); c[-1 - k] = int(c[-1 - k] * k / FADE)
    return c


def main(meta_json, out_zip):
    work = tempfile.mkdtemp(dir=eleven.VOICE_DIR)
    clips_dir = os.path.join(work, 'clips')
    os.makedirs(clips_dir)
    carriers = phrases.all_carriers()
    new_phrases, rows, n_new = [], [], 0
    for ci, (carrier, is_new) in enumerate(carriers):
        text = ' '.join(s[0] for s in carrier)
        x, words = voiced(text)
        if len(words) != len(text.split()):
            sys.exit(f'word timing mismatch for: {text}')
        w = 0
        for spoken, key, stem in carrier:
            n = len(spoken.split())
            if stem:
                if is_new:
                    n_new += 1
                    stem = f'ol{n_new:03d}'
                    new_phrases.append((key, stem))
                clip = cut(x, words, w, w + n - 1)
                write(os.path.join(clips_dir, stem + '.wav'), clip)
                rows.append({'clip': stem, 'text': key, 'new': int(is_new), 'seconds': f'{len(clip) / SR:.2f}',
                             'from_sentence': text})
            w += n
        print(f'\r{ci + 1}/{len(carriers)} sentences', end='', flush=True)
    print()
    tsv = os.path.join(work, 'phrases.tsv')
    with open(tsv, 'w', encoding='utf-8', newline='\n') as fh:
        fh.writelines(f'{t}\t{s}\n' for t, s in new_phrases)
    with open(os.path.join(work, 'clips.csv'), 'w', newline='', encoding='utf-8-sig') as fh:
        wr = csv.DictWriter(fh, fieldnames=list(rows[0])); wr.writeheader(); wr.writerows(rows)
    pack = voicepack.make(clips_dir, tsv, meta_json, out_zip)
    print(f'{out_zip}: {len(pack.clips)} clips, {len(pack.phrases)} longer phrases (working files in {work})')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
