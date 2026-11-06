# Voice packs: a new voice for the unit

Date: 2026-11-06. Code: `openlion/voice_grammar.py`, `openlion/voicepack.py`, `make_card --voice-pack`, `tests/card_check.py --voice-pack`, app Voice step (`app/src/OpenLion/ViewModels/VoiceStep.cs`, `OpenLion.Core/Voices/VoicePacks.cs`), pack tools in `tools/voice/`. Car-tested on a MY12 unit: accepted, and it sounds much smoother than the factory voice.

## How the unit speaks

`navigation/voices/` has one folder per language: `en_GB` (207 WAVs), `en_US` (211) and `fr_FR` (215). The clips are identical on the MY10, MY12, MY15 and MY17 cards; only MY10's grammar file differs, by 2 characters. Every clip is 16 kHz mono 16-bit PCM, about 2 minutes of speech per language. Each folder has a grammar file, `Language_*.bgl`, and the unit speaks in three steps:

1. **Sentence templates.** The navigation software picks a template for the manoeuvre, e.g. `VS_SIMPLE_1 = "$VX_INSTRUCTION:0 $VX_DIRECTION:0 $VX_DIST"` and `VS_SIMPLE_2 = "$VX_CONJUNCTION $VX_INSTRUCTION:1 $VX_DIRECTION:1 $VX_AGAIN"`, with conditions choosing variants.
2. **Slot values to text.** `VX_DIR_L` is "left" (or "left." at the end of a sentence), a 300 m distance is "300 meters ahead" / "300 meters ahead.", and so on. The result is a line of text: "Turn left 300 meters ahead."
3. **Text to clips.** The text is looked up in the **phrase table**, which pairs each phrase with a WAV. The table is sorted longest phrase first, and the factory set already mixes whole phrases ("Stay straight on the current road") with single words, so the unit takes the longest phrase it has a clip for.

Clip names hint at where a clip goes in a sentence: `i…` starts one, `m…` is mid-sentence and `f…` ends one, with a falling tone. That's why the factory voice sounds choppy: "Turn left 300 meters ahead." is five clips (`iturn` `mleft2` `md300` `mmeters` `fahead`), each recorded on its own.

## The grammar file

Little-endian; all text is UTF-16LE with a `u16` character count in front.

```
header   u32 257, u32 3, u32 0, u32 text_total, then grammar
         text_total = total characters of all text in the file
         (MY10 and MY12 differ by exactly the 2 characters their text differs by)
...      slot definitions and sentence templates
table    u16 count, u16 count, then count entries, longest text first:
           u8 1, u16 n, text, u16 n, clip name (WAV stem, 1-8 chars), u32 weight
           weight 700 for speech; for the pause clips ([P10ms]..[P300ms]) the pause in ms
...      distance phrases and more templates, ending "EOF"
```

`voice_grammar.read_table` finds the table by its structure (equal count pair, entries that parse, longest first), and `voice_grammar.build` adds phrases: existing entries keep their order, new ones go after the existing phrases of the same length, both counts are updated, and `text_total` goes up by `len(text) + len(clip name)` for each new phrase. With nothing added it rebuilds every factory file byte-for-byte (MY10, MY12, MY15, MY17).

## Longer phrases

Adding phrases to the table means fewer joins, and the joins that remain fall where a speaker would pause anyway:

| Sentence | Factory clips | With Elodie's phrases |
|---|---|---|
| Turn left 300 meters ahead. | 5 | 2: "Turn left" · "300 meters ahead." |
| Turn right 300 meters ahead and then immediately turn left. | 9 | 3 |
| Take the third exit. | 3 | 1 |

Elodie adds 159 phrases (207 → 366): every metric distance with and without a full stop ("50 meters ahead" … "100 kilometers ahead"), "for the next 300 meters" and the like, instruction + direction ("Turn left", "Keep slight right", "Stay straight", with and without a full stop), "and then (immediately) turn left." endings, and "at the second exit." / "Take the second exit.". On the 2026-11-06 drive the unit accepted the 366-phrase file and played the new clips.

## Voice pack format

A zip with only OpenLion's own files:

```
voicepack.json   {"format": 1, "id": "elodie-v1", "name": "Elodie", "language": "en_GB",
                  "description", "credit", "licence",
                  "clips": {clip name: SHA-256 of the WAV},
                  "phrases": [[text, clip name], ...]}
en_GB/<clip>.wav 16 kHz mono 16-bit PCM, at most 10 s
```

`voicepack.load` refuses a pack with a damaged or missing clip, another format or language, a bad clip name, or a phrase whose clip isn't in the pack. `voicepack.install(factory navigation/, pack)` returns the files for the new card: each pack clip replaces the factory clip of the same name or is added, and the user's own `Language_EnglishUK.bgl` gets the pack's phrases. The beeps, click and pauses, and the other languages, stay as they are, so no factory audio is ever in a pack. Only `voices/en_GB` may change (`build.build` refuses anything else), and `card_check --voice-pack` checks that every changed or added file is exactly what the pack makes from the factory folder and that every clip the grammar names is there and valid. A changed byte in one clip is reported as "differs from what the voice pack makes".

`make_card --voice-pack <zip>` loads and installs the pack before the map build starts, so a bad pack fails in seconds.

The US English and French voices are left alone. A custom voice only plays when the unit is set to English (UK); the app says so when a voice pack is chosen.

## In the app

The Voice step (after House numbers) lists the factory voice and each known pack (`VoicePacks.All`: id, name, GitHub release URL, size and SHA-256). **Play** plays a sample of three sentences joined the way the unit would join them: a pack's sample ships with the app (`Assets/Voices/<id>-sample.wav`, made with `python -m openlion.voicepack sample`), and the factory voice's is joined at run time from the user's own card copy (`VoiceSamples.Stock`), so the app contains no factory audio. A chosen pack is downloaded into `%LOCALAPPDATA%\OpenLion\voices` from GitHub only and kept only if its size and SHA-256 match. The build key includes the pack id, so changing the voice rebuilds the card.

## Making a voice (tools/voice)

Elodie is a voice designed with ElevenLabs Voice Design from a written description (a low, warm, relaxed Australian woman in her thirties), not a copy of anyone's voice. The voice of the EFM Toledo speaker that inspired it is of unknown origin, so it was not cloned. The chosen design was raised one semitone (`rubberband pitch=1.06`, formants kept) and spoken at speed 0.88 with `eleven_multilingual_v2`.

Generating each word on its own sounded janky: each clip had its own pitch and loudness. Instead every clip is **cut from a whole spoken sentence**:

1. `phrases.py` lists carrier sentences: for each factory clip, a natural sentence with the clip's words in the right position ("Turn | **left** | three hundred meters ahead."), and for the longer phrases, realistic instructions that each yield several clips ("**Turn right** | **300 meters ahead** | **and then immediately turn left.**"). Road numbers get a comma ("Enter the M **eight,** then turn left.") so they are said fully, as the unit reads them like a list.
2. `make_pack.py` has ElevenLabs speak each sentence once with word timings (`/with-timestamps`), brings it to −18 LUFS, and cuts each clip at the quietest 10 ms between words, with 4 ms fades, at most 80 ms of pause kept after a mid-sentence clip, and silence trimmed at sentence ends. Every sentence is cached, so rebuilding a pack costs nothing.
3. `voicepack.make` writes the pack. About 11,700 characters of speech for the whole pack.

Elodie's audio was made on a paid ElevenLabs plan, which allows this use, and is released under CC BY 4.0.

## Jimbo, and what a broad accent needs

The second pack, **Jimbo**, is an over-the-top bogan (`explicit` in its metadata; the app shows "Contains swearing"). Each voice keeps its settings in `style.json` in its own work folder (`tools/voice/style.py`): description, sample text, speech model, voice settings, pitch tweak, and an optional joke style. Jimbo's jokes (`tools/voice/jimbo.py`) only go *around* an instruction, never in place of it: "Righto, turn left" · "300 meters ahead, ya drongo." · "Take the second exit. Bloody hell." They're limited to the longer phrases and four whole-sentence clips; single words stay plain. Lines rotate so the same joke doesn't come up at every turn.

What it took to get a usable result:

- **Speech model.** `eleven_multilingual_v2` flattened his accent; `eleven_v3` (the model the voice was designed with) kept it. At stability 0 (v3's "creative" setting) the accent wandered: 3 of 5 sample clips drifted Cockney or Scottish. At 0.5 it stays put.
- **Hard words.** v3 mispronounced "galah", even respelt "guh-LAH", so it was swapped for "drongo" and "boofhead". Test any slang on its own first.
- **Word timings.** v3's own timings can be 0.1–0.2 s off, enough to cut a short word in half ("three" came out as a noise). With `"align": true`, `make_pack.py` gets the timings from ElevenLabs **forced alignment** on each recording instead (the API key needs the Forced Alignment permission). All 307 of Jimbo's sentences aligned word for word. Widening the search for a quiet cut point by 40 ms made things worse where he runs words together, so `cut_slack` is 0.
- **Words run together.** Where there's no gap to cut at, a little of the next or previous word comes along ("Um eleven", "Once again"). `"isolate": [clip names]` re-voices those clips with a comma either side of the word, giving the cut a real pause; 16 of 22 came out clean, the rest are accent.
- **Checking 358 clips.** `tools/voice/check_clips.py` transcribes every clip with ElevenLabs speech-to-text (Speech to Text permission) and flags clips whose transcript is missing words, has extra ones, or starts or ends differently. Single words are hard for speech-to-text whoever says them: Elodie, whose clips are car-tested, gets 55 of 199 single-word clips flagged and 2 of 159 longer phrases. So compare against a known-good voice and look at what's flagged for one voice only. For Jimbo that showed real problems (bits of the neighbouring word) as well as accent ("lane" heard as "line", "on your route" as "Danya Roux"). It can't judge accents: listen to the sample and a handful of clips.
- **Re-rolls.** `"rerolls": {"sentence": seed}` re-voices one sentence with another random seed; everything else comes from the cache.

Jimbo: 358 clips, 159 longer phrases, 14.3 MB, about 6,000 credits including the redo on the steadier setting.
