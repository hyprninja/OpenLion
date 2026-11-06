"""Jimbo: an over-the-top bogan navigator (style.json "jokes": "jimbo").

Rule: the instruction is always said in full and in order ("Turn left", "300 meters
ahead"); the jokes and swearing only go around it, so he's still a usable navigator.
Only the longer phrases and a few whole-sentence clips get jokes; single words stay
plain. Lines rotate so the same joke doesn't come up every time.
"""

# "{x}" is the instruction as spoken (digits already in words), "{y}" the same without its
# final full stop, "{z}" the same starting lower-case (after a "Righto, ").
START = ["Righto, {z}", "{x}", "Oi, {z}", "{x}", "Strewth, {z}", "{x}", "Listen up, {z}"]
START_END = ["{y}, ya bloody drongo.", "{y}. Now. Strewth.", "{y}, mate.", "{y}, for Christ's sake.",
             "{y}, ya boofhead."]
DIST_END = ["{y}, mate.", "{y}, ya drongo.", "{y}. Bloody oath.", "{x}", "{y}, no worries.",
            "{y}, ya muppet.", "{y}. Don't stuff it up."]
DIST_MID = ["{x}", "{x}, near enough", "{x}, give or take"]
END = ["{x}", "{y}, ya mug.", "{y}, quick smart.", "{y}, ya flamin' drongo.",
       "{y}. Easy as.", "{y}, don't bugger it up."]
LONG = ["{x}, just sit tight", "{x}, keep your shirt on", "{x}", "{x}, cruise control, mate"]
EXIT = ["{y}, ya dag.", "{y}, mate.", "{x}", "{y}. Bloody hell."]

# Whole-sentence factory clips, by table text.
WHOLE = {
    "The navigation system is ready.": "Righto, the navigation system is ready. Let's get this shitbox movin'.",
    "Make a legal U-turn.": "Make a legal U-turn, ya muppet.",
    "Stay straight on the current road.": "Stay straight on the current road, mate. Don't do anything stupid.",
    "Last exit before a traffic jam.": "Last exit before a traffic jam. Bail out now or you're stuffed.",
}

_count = {}


def _pick(name, lines, x):
    k = _count.get(name, 0)
    _count[name] = k + 1
    return lines[k % len(lines)].format(x=x, y=x.rstrip("."), z=x[:1].lower() + x[1:])


def flavour(kind, spoken):
    """The spoken text for a longer phrase of this kind (see phrases.long_carriers)."""
    table = {"start": START, "start_end": START_END, "dist_end": DIST_END, "dist_mid": DIST_MID,
             "end": END, "long": LONG, "exit": EXIT}.get(kind)
    return _pick(kind, table, spoken) if table else spoken
