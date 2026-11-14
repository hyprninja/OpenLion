# 28. App states

Generated from [`app/src/OpenLion/ViewModels/Flows.cs`](../app/src/OpenLion/ViewModels/Flows.cs) by
`AppFlowTests`: don't edit it by hand. After changing a flow, run the app tests with `OPENLION_UPDATE_DOCS=1`
to rewrite it; without that, the tests fail while it's out of date.

Each machine below is the table the app runs from. Every move is written to `app.log` as
`state: <machine> <from> -> <to>`, and every step change as `state: wizard <step> -> <step>`, so a log
reads as one timeline. A move a table doesn't list is still made (the app never stops the user over it)
but is logged as `UNDECLARED`, which is a bug to fix here or in the step.

Not machines, on purpose: what a download row shows (nothing yet / latest / newer online / couldn't
check) is worked out from what's on disk and what the last check found, so it can't get out of step; and
the engine's build stages are the ordered `STAGES` list in `openlion/make_card.py`.

## activity

The foreground job on a download step (Get OSM data, Speed data, House numbers, Voice): a download, an import, or an immediate problem such as a missing engine. Checking what's online runs alongside it (IsChecking) and is not part of this machine. A stop is not a failure: it shows a neutral notice.

Logged as `osm`, `speed`, `gnaf` and `voice`, one for each step that runs it.

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Working
    Idle --> Failed
    Working --> Idle
    Working --> Failed
    Working --> Stopped
    Failed --> Idle
    Failed --> Working
    Stopped --> Idle
    Stopped --> Working
    Stopped --> Failed
```

## copy

Copy card: copy the factory card and check every file, then check OpenLion can re-create its map tables byte for byte. Only Verified lets the wizard go on.

```mermaid
stateDiagram-v2
    [*] --> Waiting
    Waiting --> Copying
    Copying --> Checking
    Copying --> Failed
    Copying --> Cancelled
    Checking --> Verified
    Checking --> Failed
    Checking --> Cancelled
    Verified --> Copying
    Failed --> Copying
    Cancelled --> Copying
```

## build

Build maps: run the engine. Entering the step lands on Done (an earlier build still matches), Ready, or Blocked (the copy or the OSM data is missing). Only Done lets the wizard go on.

```mermaid
stateDiagram-v2
    [*] --> Ready
    Ready --> Building
    Ready --> Done
    Ready --> Blocked
    Building --> Done
    Building --> Failed
    Building --> Cancelled
    Done --> Ready
    Done --> Building
    Failed --> Ready
    Failed --> Building
    Failed --> Done
    Cancelled --> Ready
    Cancelled --> Building
    Cancelled --> Done
    Blocked --> Ready
    Blocked --> Done
```

## write

Write new card: choose a card CardGuard allows, then write, read back and check. The guard runs again on a fresh drive list right before Writing.

```mermaid
stateDiagram-v2
    [*] --> Choosing
    Choosing --> Writing
    Writing --> Done
    Writing --> Failed
    Done --> Choosing
    Failed --> Choosing
```

## eject

After a write, on macOS: eject the card from the app. Writing again starts over at None.

```mermaid
stateDiagram-v2
    [*] --> None
    None --> Ready
    Ready --> None
    Ready --> Ejecting
    Ejecting --> Ejected
    Ejecting --> Failed
    Ejected --> None
    Failed --> None
    Failed --> Ejecting
```
