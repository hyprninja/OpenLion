# Making a voice pack

How the unit speaks, the pack format and how clips are cut: [docs/26_voice_packs.md](../../docs/26_voice_packs.md).

You need an ElevenLabs account on a paid plan (the free plan doesn't allow commercial use or redistribution), ffmpeg with the rubberband filter on the PATH, and your own factory card copy.

```
set OPENLION_VOICE_WORK=D:\voice-work          (outside the repo: API key, voice.json, cache)
set OPENLION_FACTORY_GRAMMAR=<card copy>\navigation\voices\en_GB\Language_EnglishUK.bgl
echo <your API key> > %OPENLION_VOICE_WORK%\elevenlabs_key.txt

python tools/voice/eleven.py design %OPENLION_VOICE_WORK%\round1     3 designed voices to listen to
python tools/voice/eleven.py remix %OPENLION_VOICE_WORK%\round2 "slightly higher, a bit quicker"
python tools/voice/eleven.py save <generated_voice_id> "My voice"   writes voice.json
python tools/voice/make_pack.py meta.json openlion-voice-myvoice-v1.zip
python -m openlion.voicepack check openlion-voice-myvoice-v1.zip <card copy>\navigation
python -m openlion.voicepack sample openlion-voice-myvoice-v1.zip <card copy>\navigation myvoice-v1-sample.wav
```

`meta.json` holds `id`, `name`, `description`, `credit` and `licence` (and `"explicit": true` for a voice that swears). Each voice's settings go in `style.json` in its work folder: see `style.py` for the fields (description, model, voice settings, pitch tweak, jokes, alignment, re-rolls, isolated clips). Without one you get Elodie's settings. The sentences are in `phrases.py`, Jimbo's jokes in `jimbo.py`.

Check every clip before publishing (needs the Speech to Text permission; compare with a known-good voice, see docs/26):

```
python tools/voice/check_clips.py openlion-voice-myvoice-v1.zip %OPENLION_VOICE_WORK%\clip_check.tsv
```

To offer a pack in the app: publish the zip as a GitHub release asset, add it to `VoicePacks.All` in `app/src/OpenLion.Core/Voices/VoicePacks.cs` with its URL, size and SHA-256, and put its sample in `app/src/OpenLion/Assets/Voices/<id>-sample.wav`.

Never commit `elevenlabs_key.txt` or `voice.json` (both are in `.gitignore`), and only design voices from a description or from a speaker who agreed to it, never by copying someone's voice.
