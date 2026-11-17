# VY/VZ map disc research helpers

Read-only tools for taking apart the factory map disc (docs 29-33). They never write a
disc.

| File | What it does |
|---|---|
| `common.py` | Loads a disc. Gives each decodable road tile as a model (nodes, roads, shape points, groups) and splits a codec 0x0E street tile into its parts (params, raw s3/s9/s10, bit stream). Also locates the edge-node records, and maps street names from the plain search units onto tiles |
| `check_edge_records.py` | Re-proves doc 33's edge-record result: all 5,950 codec street tiles hold exactly n6 records; 153,366 of 153,366 partners point back |
| `check_codec_e.py` | Runs `openlion/carin_codec_e.py` over every codec 0x0E street tile and checks the result (doc 33): 5,950 of 5,950 pass the structure, name-tail and string checks |
| `check_plain.py` | Unpacks every codec 0x0E street tile to plain bytes (`carin_codec_e.to_plain`), checks the round trip through `carin_tile`, and runs the street rules of docs 31-32 on the town tiles |
| `codec_e_strings.py` | Prints a codec 0x0E tile's string block (fragment dictionary, coded bits, string offsets). Holds the tokenizer and prefix-code solver used while the string code was being worked out |

```
python tools/carin_research/check_edge_records.py "<path>/1-control-factory.iso"
```

The factory disc and the test discs live outside the repo, in
`Other Cards/VZ Test Discs`, because they're the owner's disc images.
