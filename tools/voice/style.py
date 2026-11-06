"""Per-voice settings: style.json in the voice's work folder (OPENLION_VOICE_WORK).

  {"description": "...",        voice design description (eleven.py design)
   "sample_text": "...",        what the designed voices read (100-1000 characters)
   "voice_settings": {...},     ElevenLabs stability / similarity_boost / style / speed
   "tweak": "ffmpeg filter",    applied to everything spoken ("anull" for none)
   "jokes": "jimbo",            optional: extra words around the longer phrases (jimbo.py)
   "model": "eleven_v3",        optional speech model (default eleven_multilingual_v2)
   "rerolls": {"sentence": 8},  optional: re-voice these sentences with another seed
   "align": true,               optional: cut by forced-alignment timings (needed for eleven_v3)
   "cut_slack": 0.0,            optional: seconds to widen the search for a quiet cut point
   "isolate": ["mleft2"]}       optional: re-voice these clips with a pause either side

Anything left out keeps Elodie's settings, the first voice.
"""
import json
import os
import sys

DEFAULTS = {
    "description": (
        "A warm, low-pitched Australian woman in her thirties with a relaxed, calm, smooth and slightly "
        "husky voice. Gentle general Australian accent. She speaks slowly and clearly, with a soft, "
        "reassuring, almost intimate tone, like a friendly in-car announcer. Clean studio recording, no background noise."),
    "sample_text": (
        "The navigation system is ready. In three hundred metres, turn left. At the next roundabout, "
        "take the second exit. Stay straight on the current road. You have arrived at your destination. "
        "Power on. Bluetooth mode."),
    "voice_settings": {"stability": 0.6, "similarity_boost": 0.8, "style": 0.0, "speed": 0.88,
                       "use_speaker_boost": True},
    # Elodie: the designed voice raised one semitone (picked by ear).
    "tweak": "rubberband=pitch=1.06:formant=preserved",
    "jokes": None,
}

WORK = os.environ.get("OPENLION_VOICE_WORK") or sys.exit("Set OPENLION_VOICE_WORK to a folder outside the repo")
_path = os.path.join(WORK, "style.json")
STYLE = dict(DEFAULTS)
if os.path.exists(_path):
    with open(_path, encoding="utf-8") as _f:
        STYLE.update(json.load(_f))
