"""What gets voiced: carrier sentences, and which clips are cut from each.

A carrier is a list of segments (spoken text, table text or None, clip name or None).
Segments with a clip name are cut out as clips; the rest only give context.
Spoken text can differ from table text (the table writes "300", we say "three hundred").
"""
import os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(HERE)))
from openlion import voice_grammar  # noqa: E402
sys.path.insert(0, HERE)
from style import STYLE  # noqa: E402
if STYLE.get('jokes') == 'jimbo':
    import jimbo as JOKES  # noqa: E402
else:
    JOKES = None
# The factory grammar file the base clips come from (the user's own card copy).
FACTORY_GRAMMAR = os.environ.get('OPENLION_FACTORY_GRAMMAR')
SOUNDS = {'hibp', 'lobp', 'keyclick', '10msp', '30msp', '50msp', '100msp', '300msp'}

ONES = 'zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen ' \
       'sixteen seventeen eighteen nineteen'.split()
TENS = {20: 'twenty', 30: 'thirty', 40: 'forty', 50: 'fifty', 60: 'sixty', 70: 'seventy', 80: 'eighty', 90: 'ninety'}


def words(n):
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n - n % 10] + ('' if n % 10 == 0 else '-' + ONES[n % 10])
    if n < 1000:
        return ONES[n // 100] + ' hundred' + ('' if n % 100 == 0 else ' ' + words(n % 100))
    return ONES[n // 1000] + ' thousand' + ('' if n % 1000 == 0 else ' ' + words(n % 1000))


def speak(text):
    """Table text -> what to say: digits become words, letters are kept."""
    out = []
    for w in text.split():
        core = w.rstrip('.,:!')
        out.append(words(int(core)) + w[len(core):] if core.isdigit() else w)
    return ' '.join(out)


LETTERS = {'A', 'B', 'E', 'I', 'M', 'N', 'O', 'R', 'S', 'IP', 'SS', 'US'}


def base_clips():
    """(clip name, text) of every factory speech clip, read from the factory grammar file."""
    if not FACTORY_GRAMMAR:
        sys.exit('Set OPENLION_FACTORY_GRAMMAR to a factory navigation/voices/en_GB/Language_EnglishUK.bgl')
    _, table, _ = voice_grammar.read_table(open(FACTORY_GRAMMAR, 'rb').read())
    return sorted({(s, t) for t, s, w in table if w == voice_grammar.SPEECH_WEIGHT and s not in SOUNDS})


# Hand-picked context for sentence starts and ends.
START_TAIL = {
    'Attention!': 'Turn left now.', 'Improved route:': 'Turn left now.',
    'The traffic situation': 'has changed.', 'There are': 'traffic problems on your route.',
    'Turn': 'left three hundred meters ahead.', 'Keep': 'left three hundred meters ahead.',
    'Stay': 'straight on the current road.', 'Exit': 'the motorway on the left.',
    'Leave': 'the roundabout at the second exit.', 'Enter': 'the motorway.',
    'Take': 'the second exit.', 'Make a U-turn': 'three hundred meters ahead.',
    'Stay straight on the current road': 'for the next three hundred meters.',
}
END_HEAD = {
    'afterwards.': 'and turn left', 'again.': 'Turn left', 'ahead.': 'three hundred meters',
    'at the end of the road.': 'Turn left', 'at the next roundabout.': 'Turn left',
    'cannot be calculated.': 'The route', 'exit.': 'Take the second', 'feet.': 'In three hundred',
    'kilometers.': 'In three', 'kilometer.': 'In one', 'meters.': 'In three hundred',
    'mile.': 'In one', 'miles.': 'In three', 'yards.': 'In three hundred',
    'for a long time.': 'There are traffic problems', 'is being calculated.': 'The route',
    'is in the indicated direction.': 'The destination', 'is within a traffic restricted area.': 'The destination',
    'left.': 'Turn', 'right.': 'Turn', 'straight.': 'Stay', 'now.': 'Turn left', 'soon.': 'Turn left',
    'on the left.': 'The destination is', 'on the right.': 'The destination is', 'reached.': 'Destination',
    'until further notice.': 'The road is closed', 'has changed.': 'The traffic situation',
    'is outside the digitized map area.': 'The destination', 'on your route.': 'There are traffic problems',
}


# Context (before, after) for mid-sentence clips, close to how the car uses them.
MID = {}
for w in ('left', 'right', 'straight', 'slight', 'hard'):
    MID[w] = ('Turn', 'three hundred meters ahead.')
for w in ('turn', 'keep', 'stay', 'enter', 'exit', 'leave'):
    MID[w] = ('and then', 'left.')
MID.update({
    'and then': ('Turn left', 'turn right.'), 'and': ('Turn left', 'turn right.'),
    'immediately': ('and then', 'turn left.'),
    'ahead': ('Turn left three hundred meters', 'and then turn right.'),
    'hundred': ('Turn left in three', 'meters.'), 'thousand': ('Turn left in two', 'meters.'),
    'the destination': ('You will reach', 'soon.'), 'the destination road': ('You will reach', 'soon.'),
    'motorway': ('Leave the', 'at the next exit.'), 'roundabout': ('Leave the', 'at the second exit.'),
    'road': ('Take the second', 'on the right.'), 'national road': ('Enter the', 'now.'),
    'onto': ('Turn left', 'the motorway.'), 'at': ('Leave the roundabout', 'the second exit.'),
    'on the left': ('Take the second exit', 'now.'), 'on the right': ('Take the second exit', 'now.'),
    'at the end of the road': ('Turn left', 'and then turn right.'), 'is': ('The destination', 'ahead.'),
    'for the next': ('Stay on the road', 'three hundred meters.'),
    'change to the parallel road': ('Please', 'now.'), 'possibility': ('Take the next', 'on the right.'),
    'half a': ('Turn left in', 'mile.'), 'quarter of a': ('Turn left in a', 'mile.'),
    'a quarter of a': ('Turn left in', 'mile.'), 'three quarters of a': ('Turn left in', 'mile.'),
    'and a half': ('Turn left in one', 'miles.'), 'and a quarter': ('Turn left in one', 'miles.'),
    'and three quarters': ('Turn left in one', 'miles.'),
})
for w in ('meters', 'feet', 'yards'):
    MID[w] = ('Turn left three hundred', 'ahead.')
for w in ('kilometers', 'miles'):
    MID[w] = ('Turn left three', 'ahead.')
MID['stay'] = ('and then', 'straight.')
MID['straight'] = ('Stay', 'on the current road.')
for w in ('kilometer', 'mile'):
    MID[w] = ('Turn left one', 'ahead.')
SPOKEN = {'exitSBST': 'exit'}   # odd factory label; the clip just says "exit"


def base_carrier(stem, text):
    s = stem.lower()
    seg = (SPOKEN.get(text) or speak(text), text, stem)
    if JOKES and text in JOKES.WHOLE:
        return [(JOKES.WHOLE[text], text, stem)]
    if text in LETTERS:
        return [('Enter the', None, None), (' '.join(text), text, stem), ('motorway.', None, None)]
    if s.startswith('mthe') or (s.startswith('m_') and s[2:3].isdigit()):
        return [('Take', None, None), seg, ('exit.', None, None)]
    if s.startswith('md') and (text.isdigit() or text in ONES):
        unit = 'kilometers' if not text.isdigit() or int(text) <= 10 else 'meters'
        return [('Turn left in', None, None), seg, (unit + '.', None, None)]
    if s.startswith('mn') and text.isdigit():
        # Road numbers are read like a list ("M 8"); the comma makes her say them fully.
        return [('Enter the M', None, None), (speak(text) + ',', text, stem), ('then turn left.', None, None)]
    if SPOKEN.get(text, text) in MID:
        before, after = MID[SPOKEN.get(text, text)]
        return [(before, None, None), seg, (after, None, None)]
    if text[:1].isupper() and text.endswith(('.', '!', ':')) and text not in START_TAIL:
        return [seg]                                       # whole sentence on its own
    if s[0] == 'i' or text in START_TAIL:
        return [seg, (START_TAIL.get(text, 'is three hundred meters ahead.'), None, None)]
    if s[0] == 'f':
        return [(END_HEAD.get(text, 'And then'), None, None), seg]
    return [('There is', None, None), seg, ('ahead.', None, None)]   # traffic message words


# ---- longer phrases (new table entries) ----
DISTS = [f'{m} meters' for m in (50, 100, 150, 200, 300, 400, 500, 600, 700, 800, 900)] + \
        ['one kilometer'] + [f'{ONES[k]} kilometers' for k in range(2, 11)] + \
        [f'{k} kilometers' for k in list(range(11, 21)) + [30, 40, 50, 60, 70, 80, 90, 100]]
LONG_DISTS = [f'for the next {m} meters' for m in (100, 150, 200, 300, 400, 500, 600, 700, 800, 900)] + \
             ['for the next one kilometer'] + [f'for the next {ONES[k]} kilometers' for k in range(2, 11)]
DIRS = ['left', 'right', 'slight left', 'slight right', 'hard left', 'hard right']
STARTS = [f'Turn {d}' for d in DIRS] + ['Keep left', 'Keep right', 'Keep slight left', 'Keep slight right',
                                       'Stay straight']
ENDS = [f'{c} {i} {d}.' for c in ('and then', 'and then immediately', 'and')
        for i, ds in (('turn', DIRS), ('keep', ['left', 'right']), ('stay', ['straight'])) for d in ds]
ORD = ['first', 'second', 'third', 'fourth', 'fifth', 'sixth']


def long_carriers():
    """Carriers for the new longer phrases; each table text is cut exactly once."""
    out, done = [], set()

    def seg(text, kind):
        clip = None if text in done else text
        done.add(text)
        spoken = speak(text)
        if JOKES and clip:          # only the clips that get cut carry the jokes
            spoken = JOKES.flavour(kind, spoken)
        return (spoken, clip, clip)

    for k, d in enumerate(DISTS):                                  # "Turn left" + "300 meters ahead."
        out.append([seg(STARTS[k % len(STARTS)], 'start'), seg(f'{d} ahead.', 'dist_end')])
    for k, d in enumerate(DISTS):                                  # "... 300 meters ahead" + "and then turn left."
        out.append([(speak(STARTS[(k + 3) % len(STARTS)]), None, None), seg(f'{d} ahead', 'dist_mid'),
                    seg(ENDS[k % len(ENDS)], 'end')])
    for e in ENDS:                                                 # any "and then ..." not covered yet
        if e not in done:
            out.append([('Turn left three hundred meters ahead', None, None), seg(e, 'end')])
    for a in STARTS:                                               # "Turn left." on its own
        out.append([seg(a + '.', 'start_end')])
    for d in LONG_DISTS:
        spoken = speak(d) + '.'
        if JOKES:
            spoken = JOKES.flavour('long', speak(d)) + '.'
        out.append([('Stay straight on the current road', None, None), (spoken, d, d)])
    for o in ORD:
        out.append([('Leave the roundabout', None, None), seg(f'at the {o} exit.', 'exit')])
        out.append([seg(f'Take the {o} exit.', 'exit')])
    return out


def isolate(carrier):
    """The same sentence with a short pause either side of the clip (commas), so the cut
    lands in a real gap. For clips the voice runs into its neighbours (style "isolate")."""
    out = []
    for k, (spoken, key, stem) in enumerate(carrier):
        if stem and k > 0 and not out[-1][0].endswith((',', '.', '!', ':')):
            out[-1] = (out[-1][0] + ',',) + out[-1][1:]
        if stem and k < len(carrier) - 1 and not spoken.endswith((',', '.', '!', ':')):
            spoken += ','
        out.append((spoken, key, stem))
    return out


def all_carriers():
    """(carrier, is_new) for every sentence to voice."""
    iso = set(STYLE.get('isolate', []))
    return [(isolate(c) if s in iso else c, False) for s, t in base_clips() for c in [base_carrier(s, t)]] +            [(c, True) for c in long_carriers()]


if __name__ == '__main__':
    cs = all_carriers()
    n_new = sum(1 for c, new in cs if new for s in c if s[2])
    chars = sum(len(' '.join(s[0] for s in c)) for c, _ in cs)
    print(len(cs), 'carriers;', len(base_clips()), 'base clips;', n_new, 'new phrases;', chars, 'characters')
    for c, _ in cs[::25]:
        print(' | '.join(f'[{s[0]}]' if s[2] else s[0] for s in c))
