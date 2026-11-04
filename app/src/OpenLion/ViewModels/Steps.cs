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
    public string Version => Card?.Volume ?? "navigation folder found, but no cdrom.txt";
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
    }

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

/// <summary>Steps not built yet: copy, OSM download, build.</summary>
public sealed class PlaceholderStep(string title, string text) : StepViewModel(title)
{
    public string Text { get; } = text;
    public override bool CanGoNext => true;
}

public sealed record TargetCandidate(GuardResult Guard)
{
    public string Heading => Guard.Target.ToString();
    public bool Allowed => Guard.Allowed;
    public string Verdict => Guard.Allowed ? "OK to write" : "Will not write to this drive";
    public IEnumerable<string> Reasons => Guard.Findings.Select(f => f.Message);
}

public sealed class WriteCardStep : StepViewModel
{
    readonly Session _session;
    readonly DrivePoller _poller = new();

    public ObservableCollection<TargetCandidate> Targets { get; } = [];
    public bool NoneFound => Targets.Count == 0;
    public override bool CanGoNext => false;

    public WriteCardStep(Session session) : base("Write new card")
    {
        _session = session;
        _poller.Updated += Refresh;
    }

    void Refresh(IReadOnlyList<DriveSnapshot> drives)
    {
        Targets.Clear();
        // Every removable drive is shown with the guard's verdict, so the user sees why a card is refused.
        foreach (var d in drives.Where(d => d.IsRemovable || d.HasNavigationFolder))
            Targets.Add(new TargetCandidate(CardGuard.CheckTarget(d, drives.ToList(), _session.FactoryRecord,
                _session.RequiredBytes, _session.GuardOptions)));
        Raise(nameof(NoneFound));
    }

    public override void OnEnter() => _poller.Start();
    public override void OnLeave() => _poller.Stop();
}
