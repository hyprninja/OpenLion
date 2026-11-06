"""ElevenLabs helper for designing a new nav voice and speaking prompts.

The API key is read from the ELEVENLABS_API_KEY environment variable, or from
elevenlabs_key.txt in OPENLION_VOICE_WORK (one line). It is never printed. Keep that
folder outside the repo.

  python eleven.py design <out_dir> [--seed N] [--desc "..."]
      Designs 3 preview voices from a description; saves preview_1..3.mp3 and previews.json.
  python eleven.py remix <out_dir> "<change wanted>" [--seed N]
      3 variations of the saved voice; saves remix_1..3.mp3 and previews.json.
  python eleven.py save <generated_voice_id> <name>
      Turns a preview into a saved voice; writes voice/voice.json.
  python eleven.py say <out.wav> <text> [--model ID] [--prev TEXT] [--next TEXT]
      Speaks text with the saved voice, applies TWEAK, and writes a 16 kHz mono
      16-bit WAV (the car's format). --prev/--next give surrounding words so a
      fragment gets the right intonation (not supported by eleven_v3).
"""
import base64, http.client, json, os, subprocess, sys, time, urllib.error, urllib.request, wave

HERE = os.path.dirname(os.path.abspath(__file__))
# Work folder OUTSIDE the repo: the API key, the chosen voice (voice.json), style.json and the cache.
sys.path.insert(0, HERE)
from style import STYLE, WORK as VOICE_DIR  # noqa: E402
API = 'https://api.elevenlabs.io'

DESCRIPTION = STYLE['description']
SAMPLE_TEXT = STYLE['sample_text']
VOICE_SETTINGS = STYLE['voice_settings']
TWEAK = STYLE['tweak']
MODEL = STYLE.get('model', 'eleven_multilingual_v2')   # speech model for the clips


def key():
    k = os.environ.get('ELEVENLABS_API_KEY')
    if not k:
        p = os.path.join(VOICE_DIR, 'elevenlabs_key.txt')
        if os.path.exists(p):
            k = open(p, encoding='utf-8-sig').read().strip()
    if not k:
        sys.exit('No API key: set ELEVENLABS_API_KEY or create elevenlabs_key.txt in OPENLION_VOICE_WORK')
    return k


def post(path, body):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), method='POST',
                                 headers={'xi-api-key': key(), 'Content-Type': 'application/json'})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code in (409, 429, 500, 502, 503) and attempt < 3:
                time.sleep(3 * (attempt + 1)); continue
            sys.exit(f'ElevenLabs error {e.code}: {e.read().decode(errors="replace")[:500]}')
        except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))


def arg(flag, default=None):
    a = sys.argv
    return a[a.index(flag) + 1] if flag in a else default


def design(out_dir):
    os.makedirs(out_dir, exist_ok=True)
    body = {'voice_description': arg('--desc', DESCRIPTION), 'text': SAMPLE_TEXT,
            'model_id': 'eleven_ttv_v3', 'guidance_scale': 3}
    if arg('--seed'):
        body['seed'] = int(arg('--seed'))
    res = json.loads(post('/v1/text-to-voice/design', body))
    meta = []
    for i, p in enumerate(res['previews'], 1):
        path = os.path.join(out_dir, f'preview_{i}.mp3')
        open(path, 'wb').write(base64.b64decode(p['audio_base_64']))
        meta.append({'file': os.path.basename(path), 'generated_voice_id': p['generated_voice_id'],
                     'seconds': p.get('duration_secs')})
        print(path)
    json.dump({'description': body['voice_description'], 'text': res.get('text'), 'previews': meta},
              open(os.path.join(out_dir, 'previews.json'), 'w'), indent=1)


def remix(out_dir, change):
    """Variations of the saved voice, described by the change wanted."""
    os.makedirs(out_dir, exist_ok=True)
    voice = json.load(open(os.path.join(VOICE_DIR, 'voice.json')))['voice_id']
    body = {'voice_description': change, 'text': SAMPLE_TEXT, 'guidance_scale': 2}
    if arg('--seed'):
        body['seed'] = int(arg('--seed'))
    res = json.loads(post(f'/v1/text-to-voice/{voice}/remix', body))
    meta = []
    for i, p in enumerate(res['previews'], 1):
        path = os.path.join(out_dir, f'remix_{i}.mp3')
        open(path, 'wb').write(base64.b64decode(p['audio_base_64']))
        meta.append({'file': os.path.basename(path), 'generated_voice_id': p['generated_voice_id'],
                     'seconds': p.get('duration_secs')})
        print(path)
    json.dump({'base_voice': voice, 'change': change, 'previews': meta},
              open(os.path.join(out_dir, 'previews.json'), 'w'), indent=1)


def save(gen_id, name):
    res = json.loads(post('/v1/text-to-voice', {'voice_name': name, 'voice_description': DESCRIPTION,
                                                'generated_voice_id': gen_id}))
    json.dump({'voice_id': res['voice_id'], 'name': name, 'generated_voice_id': gen_id,
               'description': DESCRIPTION}, open(os.path.join(VOICE_DIR, 'voice.json'), 'w'), indent=1)
    print('saved voice', res['voice_id'])


def say(out, text, model=None, prev=None, nxt=None):
    """Speak text, apply the chosen tweak, and write a 16 kHz mono 16-bit WAV."""
    voice = json.load(open(os.path.join(VOICE_DIR, 'voice.json')))['voice_id']
    model = model or arg('--model', 'eleven_multilingual_v2')
    body = {'text': text, 'model_id': model, 'voice_settings': VOICE_SETTINGS, 'seed': 7}
    prev, nxt = prev or arg('--prev'), nxt or arg('--next')
    if prev and model != 'eleven_v3':
        body['previous_text'] = prev
    if nxt and model != 'eleven_v3':
        body['next_text'] = nxt
    pcm = post(f'/v1/text-to-speech/{voice}?output_format=pcm_24000', body)
    raw = out + '.raw.wav'
    with wave.open(raw, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(pcm)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', raw, '-af', TWEAK + ',aresample=16000',
                    '-ac', '1', '-c:a', 'pcm_s16le', out], check=True)
    os.remove(raw)
    print(out)


def say_timed(out, text, seed=7):
    """Speak a whole sentence; return its words with start/end times in seconds.

    Writes the tweaked 16 kHz WAV to out. Each word is (text, start, end), with
    punctuation kept on the word it follows.
    """
    voice = json.load(open(os.path.join(VOICE_DIR, 'voice.json')))['voice_id']
    body = {'text': text, 'model_id': MODEL, 'voice_settings': VOICE_SETTINGS,
            'seed': seed, 'apply_text_normalization': 'off'}
    res = json.loads(post(f'/v1/text-to-speech/{voice}/with-timestamps?output_format=pcm_24000', body))
    pcm = base64.b64decode(res['audio_base64'])
    raw = out + '.raw.wav'
    with wave.open(raw, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(24000); w.writeframes(pcm)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', raw, '-af', TWEAK + ',aresample=16000',
                    '-ac', '1', '-c:a', 'pcm_s16le', out], check=True)
    os.remove(raw)
    a = res['alignment']
    words, cur = [], None
    for ch, s, e in zip(a['characters'], a['character_start_times_seconds'], a['character_end_times_seconds']):
        if ch.isspace():
            cur = None; continue
        if cur is None:
            cur = [ch, s, e, s, e]; words.append(cur)   # text, start, end, speech start, speech end
        else:
            cur[0] += ch; cur[2] = e
            if ch.isalnum():
                cur[4] = e
    return [(w[0], w[3], w[4]) for w in words]


def align(wav_path, text):
    """Exact word timings for a recording of text (ElevenLabs forced alignment):
    [(word, start, end)], one per word of text.split(). eleven_v3's own timings can be
    0.1-0.2 s off, enough to cut a short word in half."""
    boundary = 'openlion' + os.urandom(8).hex()
    with open(wav_path, 'rb') as f:
        audio = f.read()
    nl = (chr(13) + chr(10)).encode()
    b = f'--{boundary}'.encode()
    body = (b + nl + b'Content-Disposition: form-data; name="text"' + nl + nl + text.encode() + nl +
            b + nl + b'Content-Disposition: form-data; name="file"; filename="a.wav"' + nl +
            b'Content-Type: audio/wav' + nl + nl + audio + nl + b + b'--' + nl)
    req = urllib.request.Request(API + '/v1/forced-alignment', data=body, method='POST',
                                 headers={'xi-api-key': key(), 'Content-Type': f'multipart/form-data; boundary={boundary}'})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                res = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            if e.code in (409, 429, 500, 502, 503) and attempt < 3:
                time.sleep(3 * (attempt + 1)); continue
            sys.exit(f'ElevenLabs alignment error {e.code}: {e.read().decode(errors="replace")[:500]}')
        except (http.client.IncompleteRead, urllib.error.URLError, TimeoutError, ConnectionError):
            if attempt == 3:
                raise
            time.sleep(3 * (attempt + 1))
    words = [w for w in res['words'] if w['text'].strip()]
    if len(words) != len(text.split()):
        return None
    return [(t, w['start'], w['end']) for t, w in zip(text.split(), words)]


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    if cmd == 'design':
        design(sys.argv[2])
    elif cmd == 'credits':
        req = urllib.request.Request(API + '/v1/user/subscription', headers={'xi-api-key': key()})
        s = json.loads(urllib.request.urlopen(req, timeout=60).read())
        print(s.get('tier'), 'used', s.get('character_count'), 'of', s.get('character_limit'),
              'resets', s.get('next_character_count_reset_unix'))
    elif cmd == 'remix':
        remix(sys.argv[2], sys.argv[3])
    elif cmd == 'save':
        save(sys.argv[2], sys.argv[3])
    elif cmd == 'say':
        say(sys.argv[2], sys.argv[3])
    else:
        print(__doc__)
