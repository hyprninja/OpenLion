using OpenLion.Core.Flow;

namespace OpenLion.ViewModels;

/// <summary>
/// Every state machine in the wizard, in one place. The steps run from these tables, the tests check
/// them, and docs/28_app_states.md is generated from them (AppFlowTests). To change a flow, change the
/// table here, then the step, then regenerate the doc.
/// </summary>
public static class Flows
{
    public static readonly StateFlow<ActivityState> Activity = new("activity",
        "The foreground job on a download step (Get OSM data, Speed data, House numbers, Voice): a download, " +
        "an import, or an immediate problem such as a missing engine. Checking what's online runs alongside it " +
        "(IsChecking) and is not part of this machine. A stop is not a failure: it shows a neutral notice.",
        ActivityState.Idle,
        (ActivityState.Idle, [ActivityState.Working, ActivityState.Failed]),
        (ActivityState.Working, [ActivityState.Idle, ActivityState.Failed, ActivityState.Stopped]),
        (ActivityState.Failed, [ActivityState.Idle, ActivityState.Working]),
        (ActivityState.Stopped, [ActivityState.Idle, ActivityState.Working, ActivityState.Failed]));

    public static readonly StateFlow<CopyPhase> Copy = new("copy",
        "Copy card: copy the factory card and check every file, then check OpenLion can re-create its map " +
        "tables byte for byte. Only Verified lets the wizard go on.",
        CopyPhase.Waiting,
        (CopyPhase.Waiting, [CopyPhase.Copying]),
        (CopyPhase.Copying, [CopyPhase.Checking, CopyPhase.Failed, CopyPhase.Cancelled]),
        (CopyPhase.Checking, [CopyPhase.Verified, CopyPhase.Failed, CopyPhase.Cancelled]),
        (CopyPhase.Verified, [CopyPhase.Copying]),
        (CopyPhase.Failed, [CopyPhase.Copying]),
        (CopyPhase.Cancelled, [CopyPhase.Copying]));

    public static readonly StateFlow<BuildPhase> Build = new("build",
        "Build maps: run the engine. Entering the step lands on Done (an earlier build still matches), " +
        "Ready, or Blocked (the copy or the OSM data is missing). Only Done lets the wizard go on.",
        BuildPhase.Ready,
        (BuildPhase.Ready, [BuildPhase.Building, BuildPhase.Done, BuildPhase.Blocked]),
        (BuildPhase.Blocked, [BuildPhase.Ready, BuildPhase.Done]),
        (BuildPhase.Building, [BuildPhase.Done, BuildPhase.Failed, BuildPhase.Cancelled]),
        (BuildPhase.Done, [BuildPhase.Ready, BuildPhase.Building]),
        (BuildPhase.Failed, [BuildPhase.Ready, BuildPhase.Done, BuildPhase.Building]),
        (BuildPhase.Cancelled, [BuildPhase.Ready, BuildPhase.Done, BuildPhase.Building]));

    public static readonly StateFlow<WriteStepPhase> Write = new("write",
        "Write new card: choose a card CardGuard allows, then write, read back and check. The guard runs " +
        "again on a fresh drive list right before Writing.",
        WriteStepPhase.Choosing,
        (WriteStepPhase.Choosing, [WriteStepPhase.Writing]),
        (WriteStepPhase.Writing, [WriteStepPhase.Done, WriteStepPhase.Failed]),
        (WriteStepPhase.Failed, [WriteStepPhase.Choosing]),
        (WriteStepPhase.Done, [WriteStepPhase.Choosing]));

    public static readonly StateFlow<EjectPhase> Eject = new("eject",
        "After a write, on macOS: eject the card from the app. Writing again starts over at None.",
        EjectPhase.None,
        (EjectPhase.None, [EjectPhase.Ready]),
        (EjectPhase.Ready, [EjectPhase.Ejecting, EjectPhase.None]),
        (EjectPhase.Ejecting, [EjectPhase.Ejected, EjectPhase.Failed]),
        (EjectPhase.Failed, [EjectPhase.Ejecting, EjectPhase.None]),
        (EjectPhase.Ejected, [EjectPhase.None]));

    public static IReadOnlyList<IStateFlow> All => [Activity, Copy, Build, Write, Eject];
}
