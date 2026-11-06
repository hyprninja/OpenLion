using System.Collections.ObjectModel;
using System.IO;
using OpenLion.Core;
using OpenLion.Core.Cards;
using OpenLion.Core.Drives;

namespace OpenLion.ViewModels;

/// <summary>State shared between wizard steps.</summary>
public sealed class Session
{
    public AppState State { get; } = AppState.Load();
    public DriveSnapshot? FactoryDrive { get; set; }
    public NavigationCard? FactoryCard { get; set; }

    /// <summary>Set by the copy step once every file is hash-verified and the map format check passed.
    /// Starts as the card verified in an earlier run, if any.</summary>
    public FactoryCardRecord? FactoryRecord { get; set; }
    public CopyResult? FactoryCopy { get; set; }

    /// <summary>The countries to put on the card (chosen on the Get OSM data step).</summary>
    public IReadOnlyList<OpenLion.Core.Osm.MapCountry> Countries { get; set; } = [OpenLion.Core.Osm.MapCountry.Australia];

    /// <summary>One OSM extract per country in <see cref="Countries"/>, in the same order (set by the
    /// Get OSM data step once every one is on this PC).</summary>
    public IReadOnlyList<OpenLion.Core.Osm.OsmExtract>? Osm { get; set; }

    /// <summary>"OpenStreetMap data for New Zealand from 3 November 2026" etc.</summary>
    public string DescribeOsm()
    {
        if (Osm is not { Count: > 0 } osm)
            return "";
        if (osm.Count == 1)
            return $"{osm[0].Describe()} ({OpenLion.Core.Osm.MapCountry.Describe(Countries)})";
        var parts = Countries.Zip(osm, (c, e) => $"{c.Title}: {e.Describe()}");
        return "OpenStreetMap data for " + OpenLion.Core.Osm.MapCountry.Describe(Countries) + " (" + string.Join("; ", parts) + ")";
    }

    /// <summary>Prepared speed zone data to add (set by the Speed data step; empty when none is chosen,
    /// or when Australia isn't on the card).</summary>
    public IReadOnlyList<OpenLion.Core.Speed.SpeedZoneData> SpeedZones { get; set; } = [];

    /// <summary>The G-NAF release to add house numbers from (set by the House numbers step; null when
    /// not wanted, or when Australia isn't on the card).</summary>
    public OpenLion.Core.Addresses.GnafData? Gnaf { get; set; }

    /// <summary>What a build is keyed on: the OSM extracts, the speed data and the G-NAF release.</summary>
    public string? BuildKey() => Osm is { } osm ? BuildRecord.Key(osm, SpeedZones, Gnaf) : null;

    /// <summary>"speed zone data for Victoria (Speed Zones September 2026)", or "" when none.</summary>
    public string DescribeSpeedZones() => SpeedZones.Count == 0 ? "" :
        "speed zone data for " + string.Join(" and ", SpeedZones.Select(z =>
            $"{OpenLion.Core.Speed.SpeedZoneSource.ByCode(z.Code)?.Title ?? z.Code} ({z.Name})"));

    /// <summary>"G-NAF August 2026", or "" when not used.</summary>
    public string DescribeGnaf() => Gnaf is null ? "" :
        OpenLion.Core.Addresses.GnafDownloader.Describe(Gnaf.FileName);

    /// <summary>The finished map build to write (set by the Build maps step).</summary>
    public BuildRecord? Build { get; set; }

    public Session() => FactoryRecord = State.Factory;

    public GuardOptions GuardOptions => new() { WrittenByOpenLion = State.WrittenVolumeSerials.ToHashSet() };

    /// <summary>Space the finished navigation/ folder needs; refined once the build has run.</summary>
    public long RequiredBytes { get; set; } = 1_000_000_000;
}

public abstract class StepViewModel(string title) : ObservableObject
{
    bool _isCurrent;
    public string Title { get; } = title;
    public bool IsCurrent { get => _isCurrent; set => Set(ref _isCurrent, value); }
    public abstract bool CanGoNext { get; }
    /// <summary>True while the step runs something that must not be left half-done (Back is disabled).</summary>
    public virtual bool IsWorking => false;
    public virtual void OnEnter() { }
    public virtual void OnLeave() { }
}

public sealed class WelcomeStep() : StepViewModel("Welcome")
{
    bool _accepted;
    public bool Accepted { get => _accepted; set => Set(ref _accepted, value); }
    public override bool CanGoNext => Accepted;
}

public sealed record FactoryCandidate(DriveSnapshot Drive, NavigationCard? Card)
{
    public string Heading => Drive.ToString();
    public string Version => Card?.DisplayName ?? "navigation folder found, but no card version file";
    public string Databases => Card is null ? "" :
        string.Join(Environment.NewLine, Card.Databases.Select(d =>
            $"{d.Folder}: {d.Name}, {d.Area}{(d.FolderExists ? "" : " (folder missing)")}"));
    public string LockHint => Drive.IsReadOnly
        ? "Write-protected. Good: Windows can't change this card."
        : "Not write-protected. For extra safety, take the card out, slide its lock switch away from the gold contacts, and put it back.";
}

public sealed class FactoryCardStep : StepViewModel
{
    readonly Session _session;
    readonly DrivePoller _poller = new();
    FactoryCandidate? _selected;

    public ObservableCollection<FactoryCandidate> Candidates { get; } = [];
    public FactoryCandidate? Selected
    {
        get => _selected;
        set { if (Set(ref _selected, value)) Raise(nameof(CanGoNext)); }
    }
    public bool NoneFound => Candidates.Count == 0;
    public override bool CanGoNext => Selected is not null;

    public FactoryCardStep(Session session) : base("Factory card")
    {
        _session = session;
        _poller.Updated += Refresh;
        UseSavedCopyCommand = new RelayCommand(() => SkipCopyRequested?.Invoke(), () => HasSavedCopy);
    }

    /// <summary>Raised when the user picks the copy made in an earlier run: go straight to the OSM step.</summary>
    public event Action? SkipCopyRequested;
    public System.Windows.Input.ICommand UseSavedCopyCommand { get; }

    /// <summary>A verified copy of a factory card from an earlier run is still on this PC, made by
    /// OpenLion 1.3 or later (older copies left out the card's config/ and other root folders).</summary>
    public bool HasSavedCopy => _session.State.Factory is { CopyVerified: true } &&
                                _session.State.FactoryCopyPath is { } p && Directory.Exists(Path.Combine(p, "navigation")) &&
                                CardCopier.CardRootOf(p) is not null;
    public string SavedCopyText => _session.State.Factory is { } f
        ? $"{CardNames.Display(f.CardVersion) ?? "Factory card"}{(f.Label.Length > 0 ? $" (label {f.Label})" : "")}, {f.TotalBytes / 1e9:N1} GB card, copied and checked."
        : "";

    void Refresh(IReadOnlyList<DriveSnapshot> drives)
    {
        var found = drives.Where(d => d.HasNavigationFolder)
            .Select(d => new FactoryCandidate(d, SafeRead(d.Root))).ToList();
        var keep = Selected?.Drive.VolumeGuidPath;
        Candidates.Clear();
        foreach (var c in found)
            Candidates.Add(c);
        Selected = found.FirstOrDefault(c => c.Drive.VolumeGuidPath == keep) ?? (found.Count == 1 ? found[0] : null);
        Raise(nameof(NoneFound));
    }

    static NavigationCard? SafeRead(string root)
    {
        try { return NavigationCard.TryRead(root); }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { return null; }
    }

    public override void OnEnter() => _poller.Start();

    public override void OnLeave()
    {
        _poller.Stop();
        _session.FactoryDrive = Selected?.Drive;
        _session.FactoryCard = Selected?.Card;
    }
}

public sealed record TargetCandidate(GuardResult Guard, bool WrittenByOpenLion)
{
    public string Heading => Guard.Target.ToString() + (Guard.Target.FileSystem.Length > 0 ? "  " + Guard.Target.FileSystem : "");
    public bool Allowed => Guard.Allowed;
    public string Verdict => !Guard.Allowed ? "Will not write to this drive" :
        WrittenByOpenLion ? "OK to write: an OpenLion card, its old maps will be replaced" : "OK to write";
    public IEnumerable<string> Reasons => Guard.Findings.Select(f => f.Message);
    public string Letter => Guard.Target.Root[..1].ToUpperInvariant();

    /// <summary>Everything shown for a drive; equal keys mean nothing on screen would change.</summary>
    public static string Key(TargetCandidate t) =>
        $"{t.Guard.Target}|{t.Guard.Target.VolumeSerial:X8}|{t.Guard.Target.FileSystem}|{t.Guard.Target.RootEntryCount}|" +
        $"{t.WrittenByOpenLion}|{t.Allowed}|{string.Join("|", t.Reasons)}";

    // Screen readers and UI Automation read the item's name from this.
    public override string ToString() => $"{Heading}: {Verdict}";
}
