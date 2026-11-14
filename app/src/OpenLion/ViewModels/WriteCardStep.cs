using System.Collections.ObjectModel;
using System.IO;
using System.Windows.Input;
using OpenLion.Core;
using OpenLion.Core.Cards;
using OpenLion.Core.Drives;
using OpenLion.Core.Flow;

namespace OpenLion.ViewModels;

public enum WriteStepPhase { Choosing, Writing, Done, Failed }

/// <summary>Ejecting the card just written, on macOS (see <see cref="Flows.Eject"/>).</summary>
public enum EjectPhase { None, Ready, Ejecting, Ejected, Failed }

/// <summary>Step 6: write the built maps to a blank card. Every drive is shown with CardGuard's
/// verdict; the user picks an allowed one and types its drive letter to confirm. The guard runs
/// again on a fresh drive list right before writing.</summary>
public sealed class WriteCardStep : StepViewModel
{
    readonly Session _session;
    readonly DrivePoller _poller = new();
    CancellationTokenSource? _cts;
    TargetCandidate? _selected;
    readonly StateMachine<WriteStepPhase> _flow = new(Flows.Write);
    readonly StateMachine<EjectPhase> _eject = new(Flows.Eject);
    string _confirm = "", _status = "", _detail = "", _error = "";
    double _percent;

    public WriteCardStep(Session session) : base("Write new card")
    {
        _session = session;
        _poller.Updated += Refresh;
        WriteCommand = new RelayCommand(() => _ = WriteAsync(), () => CanWrite);
        CancelCommand = new RelayCommand(() => _cts?.Cancel(), () => IsBusy);
        AgainCommand = new RelayCommand(() => { Phase = WriteStepPhase.Choosing; Error = ""; _poller.Start(); });
        EjectCommand = new RelayCommand(() => _ = EjectAsync(), () => IsDone && _eject.State is EjectPhase.Ready or EjectPhase.Failed);
        _eject.Moved += _ => { Raise(nameof(Eject)); Platform.RequeryCommands(); };
    }

    public ICommand WriteCommand { get; }
    public ICommand CancelCommand { get; }
    public ICommand AgainCommand { get; }

    /// <summary>macOS: eject the card just written. On Windows the user ejects it in File Explorer.</summary>
    public ICommand EjectCommand { get; }
    public bool CanEject => Platform.CanEject;
    /// <summary>Where ejecting the written card is in <see cref="Flows.Eject"/>.</summary>
    public EjectPhase Eject => _eject.State;
    public string EjectNote { get => _ejectNote; private set => Set(ref _ejectNote, value); }
    DriveSnapshot? _written;
    string _ejectNote = "";

    public ObservableCollection<TargetCandidate> Targets { get; } = [];
    public bool NoneFound => Targets.Count == 0;

    public TargetCandidate? Selected
    {
        get => _selected;
        set
        {
            if (value is { Allowed: false }) value = null; // refused drives can't be chosen
            if (Set(ref _selected, value)) { ConfirmLetter = ""; Raise(nameof(HasSelection)); Raise(nameof(ConfirmPrompt)); }
        }
    }
    public bool HasSelection => Selected is not null;
    public string ConfirmPrompt => Selected is null ? "" :
        $"Everything on {Selected.Where} ({Selected.Guard.Target.TotalBytes / 1e9:N1} GB) may be replaced. Type {Selected.Letter} to confirm:";

    public string ConfirmLetter
    {
        get => _confirm;
        set { if (Set(ref _confirm, value)) Platform.RequeryCommands(); }
    }

    /// <summary>Where the step is in <see cref="Flows.Write"/>.</summary>
    public WriteStepPhase Phase
    {
        get => _flow.State;
        private set
        {
            if (!_flow.Move(value)) return;
            Raise(); Raise(nameof(IsChoosing)); Raise(nameof(IsBusy)); Raise(nameof(IsDone)); Raise(nameof(IsFailed));
            Platform.RequeryCommands();
        }
    }
    public bool IsChoosing => Phase == WriteStepPhase.Choosing;
    public bool IsBusy => Phase == WriteStepPhase.Writing;
    public bool IsDone => Phase == WriteStepPhase.Done;
    public bool IsFailed => Phase == WriteStepPhase.Failed;
    public double Percent { get => _percent; private set => Set(ref _percent, value); }
    public string Status { get => _status; private set => Set(ref _status, value); }
    public string Detail { get => _detail; private set => Set(ref _detail, value); }
    public string Error { get => _error; private set => Set(ref _error, value); }

    public bool HasBuild => _session.Build is not null;
    public string BuildText => _session.Build is { } b
        ? $"Ready to write: {b.Files:N0} files, {b.Bytes / 1e6:N0} MB{(b.Partial ? ". This is a TEST build of a small area only." : ".")}"
        : "There are no built maps yet. Go back to Build maps first.";

    /// <summary>The factory copy's other root items (config/ ...), or null for a copy made before
    /// OpenLion 1.3, which didn't keep them. The car may not accept a card without them.</summary>
    string? CardRoot => CardCopier.CardRootOf(_session.State.FactoryCopyPath);
    public bool OldCopy => HasBuild && CardRoot is null;
    public string OldCopyText =>
        "Your copy of the factory card was made with an older version of OpenLion, which only kept its " +
        "navigation folder. The car also needs the card's other folders (such as config), so please go back to " +
        "Copy your card and copy the factory card again. You won't need to build the maps again.";

    bool CanWrite => IsChoosing && HasBuild && !OldCopy && Selected is { Allowed: true } s &&
                     string.Equals(ConfirmLetter.Trim().TrimEnd(':'), s.Letter, StringComparison.OrdinalIgnoreCase);

    public override bool CanGoNext => false;
    public override bool IsWorking => IsBusy;

    void Refresh(IReadOnlyList<DriveSnapshot> drives)
    {
        // Every removable drive is shown with the guard's verdict, so the user sees why a card is refused.
        var opts = _session.GuardOptions;
        var fresh = drives.Where(d => d.IsRemovable || d.HasNavigationFolder)
            .Select(d => new TargetCandidate(CardGuard.CheckTarget(d, drives.ToList(), _session.FactoryRecord,
                _session.RequiredBytes, opts), opts.WrittenByOpenLion.Contains(d.VolumeSerial)))
            .ToList();
        // The poller runs every 2 s. Rebuilding an unchanged list would clear the selection and the typed letter.
        if (fresh.Select(TargetCandidate.Key).SequenceEqual(Targets.Select(TargetCandidate.Key)))
            return;
        var keep = Selected?.Guard.Target;
        var typed = ConfirmLetter; // read before Clear, which deselects and empties the box
        Targets.Clear();
        foreach (var t in fresh)
            Targets.Add(t);
        var again = keep is null ? null : Targets.FirstOrDefault(t =>
            t.Guard.Target.VolumeSerial == keep.VolumeSerial && t.Guard.Target.Root == keep.Root);
        Selected = again is { Allowed: true } ? again : null;
        if (Selected is not null)
            ConfirmLetter = typed;
        Raise(nameof(NoneFound));
    }

    public override void OnEnter()
    {
        Raise(nameof(HasBuild));
        Raise(nameof(BuildText));
        Raise(nameof(OldCopy));
        Platform.RequeryCommands();
        if (IsChoosing)
            _poller.Start();
    }

    public override void OnLeave()
    {
        _poller.Stop();
        _cts?.Cancel();
    }

    async Task EjectAsync()
    {
        if (_written is not { } card || !Platform.CanEject)
            return;
        EjectNote = "";
        _eject.Move(EjectPhase.Ejecting);
        try
        {
            await Task.Run(() => Platform.Eject(card));
            AppLog.Write($"write: ejected {card}");
            EjectNote = "Ejected. You can take the card out now.";
            _written = null;
            _eject.Move(EjectPhase.Ejected);
        }
        catch (IOException e)
        {
            AppLog.Write("write: eject failed: " + e.Message);
            EjectNote = "The card couldn't be ejected: " + e.Message + " Close any window showing the card and try again.";
            _eject.Move(EjectPhase.Failed, e.Message);
        }
    }

    async Task WriteAsync()
    {
        if (!CanWrite || _session.Build is not { } build || Selected is not { } choice || CardRoot is not { } cardRoot)
            return;
        _poller.Stop();
        _cts = new CancellationTokenSource();
        var target = choice.Guard.Target;
        Phase = WriteStepPhase.Writing;
        _eject.Move(EjectPhase.None);
        Error = "";
        Status = $"Writing the new maps to {choice.Where}…";
        Detail = "";
        Percent = 0;
        var progress = new Progress<WriteProgress>(p =>
        {
            if (p.BytesTotal > 0)
                Percent = p.Phase switch
                {
                    WritePhase.Writing => 70.0 * p.BytesDone / p.BytesTotal,
                    WritePhase.Verifying => 70 + 30.0 * p.BytesDone / p.BytesTotal,
                    _ => Percent,
                };
            Status = p.Phase switch
            {
                WritePhase.Checking => "Checking the card one last time…",
                WritePhase.Writing => $"Writing the new maps to {choice.Where}…",
                WritePhase.Verifying => "Reading every file back from the card to check it…",
                _ => "Finishing…",
            };
            Detail = p.FilesTotal == 0 ? "" :
                $"{p.FilesDone:N0} of {p.FilesTotal:N0} files, {p.BytesDone / 1e6:N0} of {p.BytesTotal / 1e6:N0} MB";
        });
        // The last check before writing uses a fresh drive list, not the one on screen.
        GuardResult Gate()
        {
            var drives = DriveService.GetDrives();
            var now = drives.FirstOrDefault(d => string.Equals(d.Root, target.Root, StringComparison.OrdinalIgnoreCase))
                      ?? throw new WriteException($"The card at {choice.Where} has been removed.");
            var r = CardGuard.CheckTarget(now, drives, _session.FactoryRecord, build.Bytes, _session.GuardOptions);
            AppLog.Write($"write: final check of {now}: {(r.Allowed ? "allowed" : string.Join("; ", r.Findings.Select(f => f.Problem)))}");
            return r;
        }
        var label = _session.FactoryRecord?.Label;
        AppLog.Write($"write: {build.NavigationPath} and {cardRoot} -> {target} (serial {target.VolumeSerial:X8}), label '{label}'");
        try
        {
            var result = await Task.Run(() => CardWriter.Write(build.NavigationPath, target, Gate, label, progress, _cts.Token, cardRoot));
            if (!_session.State.WrittenVolumeSerials.Contains(result.VolumeSerial))
                _session.State.WrittenVolumeSerials.Add(result.VolumeSerial);
            _session.State.Save();
            AppLog.Write($"write: done, {result.Files} files, {result.Bytes} bytes verified; {result.LabelWarning}");
            Percent = 100;
            Status = $"Done. The new card ({choice.Where}) is written and every file was read back and checked.";
            Detail = $"{result.Files:N0} files, {result.Bytes / 1e6:N0} MB." +
                     (result.LabelWarning is null ? "" : " " + result.LabelWarning) +
                     (build.Partial ? " Remember: this is a TEST build of a small area only." : "");
            _written = target;
            EjectNote = "";
            _eject.Move(EjectPhase.None);
            _eject.Move(EjectPhase.Ready);
            Phase = WriteStepPhase.Done;
        }
        catch (OperationCanceledException)
        {
            AppLog.Write("write: cancelled");
            Error = "Stopped. The card is incomplete and won't work in the car until it's written again.";
            Phase = WriteStepPhase.Failed;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            AppLog.Write("write: failed: " + e);
            Error = e is WriteException ? e.Message : "Writing the card failed: " + e.Message;
            Phase = WriteStepPhase.Failed;
        }
    }
}
