using System.Collections.ObjectModel;
using System.IO;
using System.Windows.Input;
using OpenLion.Core;
using OpenLion.Core.Cards;
using OpenLion.Core.Drives;

namespace OpenLion.ViewModels;

public enum WriteStepPhase { Choosing, Writing, Done, Failed }

/// <summary>Step 6: write the built maps to a blank card. Every drive is shown with CardGuard's
/// verdict; the user picks an allowed one and types its drive letter to confirm. The guard runs
/// again on a fresh drive list right before writing.</summary>
public sealed class WriteCardStep : StepViewModel
{
    readonly Session _session;
    readonly DrivePoller _poller = new();
    CancellationTokenSource? _cts;
    TargetCandidate? _selected;
    WriteStepPhase _phase;
    string _confirm = "", _status = "", _detail = "", _error = "";
    double _percent;

    public WriteCardStep(Session session) : base("Write new card")
    {
        _session = session;
        _poller.Updated += Refresh;
        WriteCommand = new RelayCommand(() => _ = WriteAsync(), () => CanWrite);
        CancelCommand = new RelayCommand(() => _cts?.Cancel(), () => IsBusy);
        AgainCommand = new RelayCommand(() => { Phase = WriteStepPhase.Choosing; Error = ""; _poller.Start(); });
    }

    public ICommand WriteCommand { get; }
    public ICommand CancelCommand { get; }
    public ICommand AgainCommand { get; }

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
        $"Everything on {Selected.Letter}: ({Selected.Guard.Target.TotalBytes / 1e9:N1} GB) may be replaced. Type {Selected.Letter} to confirm:";

    public string ConfirmLetter
    {
        get => _confirm;
        set { if (Set(ref _confirm, value)) CommandManager.InvalidateRequerySuggested(); }
    }

    public WriteStepPhase Phase
    {
        get => _phase;
        private set
        {
            if (!Set(ref _phase, value)) return;
            Raise(nameof(IsChoosing)); Raise(nameof(IsBusy)); Raise(nameof(IsDone)); Raise(nameof(IsFailed));
            CommandManager.InvalidateRequerySuggested();
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

    bool CanWrite => IsChoosing && HasBuild && Selected is { Allowed: true } s &&
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
        if (IsChoosing)
            _poller.Start();
    }

    public override void OnLeave()
    {
        _poller.Stop();
        _cts?.Cancel();
    }

    async Task WriteAsync()
    {
        if (!CanWrite || _session.Build is not { } build || Selected is not { } choice)
            return;
        _poller.Stop();
        _cts = new CancellationTokenSource();
        var target = choice.Guard.Target;
        Phase = WriteStepPhase.Writing;
        Error = "";
        Status = $"Writing the new maps to {choice.Letter}:…";
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
                WritePhase.Writing => $"Writing the new maps to {choice.Letter}:…",
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
                      ?? throw new WriteException($"The card at {choice.Letter}: has been removed.");
            var r = CardGuard.CheckTarget(now, drives, _session.FactoryRecord, build.Bytes, _session.GuardOptions);
            AppLog.Write($"write: final check of {now}: {(r.Allowed ? "allowed" : string.Join("; ", r.Findings.Select(f => f.Problem)))}");
            return r;
        }
        var label = _session.FactoryRecord?.Label;
        AppLog.Write($"write: {build.NavigationPath} -> {target} (serial {target.VolumeSerial:X8}), label '{label}'");
        try
        {
            var result = await Task.Run(() => CardWriter.Write(build.NavigationPath, target, Gate, label, progress, _cts.Token));
            if (!_session.State.WrittenVolumeSerials.Contains(result.VolumeSerial))
                _session.State.WrittenVolumeSerials.Add(result.VolumeSerial);
            _session.State.Save();
            AppLog.Write($"write: done, {result.Files} files, {result.Bytes} bytes verified; {result.LabelWarning}");
            Percent = 100;
            Status = $"Done. The new card ({choice.Letter}:) is written and every file was read back and checked.";
            Detail = $"{result.Files:N0} files, {result.Bytes / 1e6:N0} MB." +
                     (result.LabelWarning is null ? "" : " " + result.LabelWarning) +
                     (build.Partial ? " Remember: this is a TEST build of a small area only." : "");
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
