using System.Diagnostics;
using System.IO;
using System.Windows.Input;
using OpenLion.Core;
using OpenLion.Core.Cards;
using OpenLion.Core.Drives;
using OpenLion.Core.Engine;
using OpenLion.Core.Flow;
using OpenLion.Core.Reports;

namespace OpenLion.ViewModels;

public enum CopyPhase { Waiting, Copying, Checking, Verified, Failed, Cancelled }

/// <summary>Step 3: copy the factory card to this PC, verify every file, then prove OpenLion can
/// re-write this card's map tables byte for byte.</summary>
public sealed class CopyCardStep : StepViewModel
{
    readonly Session _session;
    readonly StateMachine<CopyPhase> _flow = new(Flows.Copy);
    CancellationTokenSource? _cts;
    double _percent;
    string _status = "";
    string _detail = "";
    string? _reportPath;
    string _summary = "";
    uint? _doneForSerial;

    public CopyCardStep(Session session) : base("Copy card")
    {
        _session = session;
        StartCommand = new RelayCommand(() => _ = RunAsync(), () => !IsBusy);
        CancelCommand = new RelayCommand(() => _cts?.Cancel(), () => IsBusy);
        OpenIssueCommand = new RelayCommand(() => Open(ErrorReport.IssueUrl));
        ShowReportCommand = new RelayCommand(() => Platform.RevealFile(_reportPath!),
            () => _reportPath is not null);
        CopySummaryCommand = new RelayCommand(() => Platform.CopyText(_summary), () => _summary.Length > 0);
    }

    public ICommand StartCommand { get; }
    public ICommand CancelCommand { get; }
    public ICommand OpenIssueCommand { get; }
    public ICommand ShowReportCommand { get; }
    public ICommand CopySummaryCommand { get; }

    /// <summary>Where the step is in <see cref="Flows.Copy"/>.</summary>
    public CopyPhase Phase
    {
        get => _flow.State;
        private set
        {
            if (!_flow.Move(value)) return;
            Raise(); Raise(nameof(IsBusy)); Raise(nameof(IsFailed)); Raise(nameof(IsSuccess));
            Raise(nameof(CanRetry)); Raise(nameof(IsCancelled)); Raise(nameof(CanGoNext));
        }
    }

    public bool IsCancelled => Phase is CopyPhase.Cancelled;

    public bool IsBusy => Phase is CopyPhase.Copying or CopyPhase.Checking;
    public bool IsFailed => Phase is CopyPhase.Failed;
    public bool IsSuccess => Phase is CopyPhase.Verified;
    public bool CanRetry => Phase is CopyPhase.Failed or CopyPhase.Cancelled;
    public double Percent { get => _percent; private set => Set(ref _percent, value); }
    public string Status { get => _status; private set => Set(ref _status, value); }
    public string Detail { get => _detail; private set => Set(ref _detail, value); }
    public string? ReportPath { get => _reportPath; private set => Set(ref _reportPath, value); }

    public override bool IsWorking => IsBusy;
    public override bool CanGoNext => Phase == CopyPhase.Verified;

    public override void OnEnter()
    {
        var drive = _session.FactoryDrive;
        if (drive is not null && _doneForSerial == drive.VolumeSerial && IsSuccess)
            return; // already copied and checked this card in this session
        _ = RunAsync();
    }

    public override void OnLeave() => _cts?.Cancel();

    async Task RunAsync()
    {
        var drive = _session.FactoryDrive;
        var card = _session.FactoryCard;
        if (drive is null || IsBusy)
            return;
        _cts = new CancellationTokenSource();
        var ct = _cts.Token;
        ReportPath = null;
        _summary = "";
        _session.FactoryRecord = null;
        CopyResult? copy = null;
        CompatResult? compat = null;
        var step = "copy";
        try
        {
            // 1. Copy and verify
            Phase = CopyPhase.Copying;
            Status = $"Copying your card ({drive.DisplayRoot}) to this {Os.Computer}…";
            var copyProgress = new Progress<CopyProgress>(p =>
            {
                Percent = p.BytesTotal == 0 ? 0 : 50.0 * p.BytesDone / p.BytesTotal;
                Detail = $"{p.FilesDone:N0} of {p.FilesTotal:N0} files, {p.BytesDone / 1e6:N0} of {p.BytesTotal / 1e6:N0} MB";
            });
            var dest = AppPaths.EnsureInside(AppPaths.CardCopyFolder(drive), AppPaths.CardsFolder);
            AppLog.Write($"copy: {drive} -> {dest}");
            var source = await Task.Run(() => Platform.PrepareFactoryCard(drive), ct);
            copy = await Task.Run(() => CardCopier.Copy(source.Root, dest, copyProgress, ct), ct);
            var known = KnownCards.Identify(copy.Manifest);
            AppLog.Write($"copy: done, {copy.Manifest.Count} files, {copy.TotalBytes} bytes; known card: {known}");

            // 2. Map format check
            step = "compatibility check";
            Phase = CopyPhase.Checking;
            Status = "Checking that OpenLion understands this card's map format…";
            var engine = EngineLocator.Locate() ?? throw new InvalidOperationException(
                "The map engine (Python) wasn't found. Please reinstall OpenLion.");
            var checkProgress = new Progress<CompatProgress>(p =>
            {
                Percent = 50 + 50 * p.Fraction;
                Detail = $"Map table {p.TableIndex + 1} of {p.TableCount}: {p.Table}";
            });
            AppLog.Write($"compat: engine {engine.Python} ({(engine.Bundled ? "bundled" : "dev")}) at {engine.EngineRoot}");
            compat = await CompatCheck.RunAsync(engine, copy.NavigationPath, checkProgress, ct);
            AppLog.Write($"compat: ok={compat.Ok}, exit {compat.ExitCode}, failed [{string.Join(", ", compat.Failed)}]");
            if (!compat.Ok)
            {
                var what = compat.EngineError ??
                           (compat.Failed.Count > 0
                               ? $"{compat.Failed.Count} map table(s) couldn't be re-created exactly: {string.Join(", ", compat.Failed)}."
                               : $"Map tables missing from the card: {string.Join(", ", compat.Missing)}.");
                Fail(step, "OpenLion doesn't support this card yet. " + what, drive, card, copy, compat, null);
                return;
            }

            // 3. Remember the verified factory card
            _session.FactoryRecord = FactoryCardRecord.FromSnapshot(drive, card?.Volume, copyVerified: true);
            _session.FactoryCopy = copy;
            _session.State.Factory = _session.FactoryRecord;
            _session.State.FactoryCopyPath = copy.CopyRoot;
            _session.State.Save();
            _doneForSerial = drive.VolumeSerial;
            Percent = 100;
            Detail = $"{copy.Manifest.Count:N0} files, {copy.TotalBytes / 1e6:N0} MB copied and checked. " +
                     $"All {compat.Tables.Count} map tables re-created byte for byte.";
            var name = card?.DisplayName;
            Status = name is null ? "Done. Your card is supported." : $"Done. Your {name} card is supported.";
            Phase = CopyPhase.Verified;
        }
        catch (OperationCanceledException)
        {
            AppLog.Write("copy step cancelled");
            _session.UseSavedFactoryCopy(); // the copy from an earlier run is untouched
            Phase = CopyPhase.Cancelled;
            Status = "Stopped. Press Try again to start over.";
            Detail = "";
        }
        catch (Exception e)
        {
            Fail(step, e.Message, drive, card, copy, compat, e);
        }
    }

    void Fail(string step, string message, DriveSnapshot drive, NavigationCard? card, CopyResult? copy,
        CompatResult? compat, Exception? e)
    {
        AppLog.Write($"{step} failed: {message}{(e is null ? "" : Environment.NewLine + e)}");
        _session.UseSavedFactoryCopy();
        Phase = CopyPhase.Failed;
        Status = message;
        Detail = "";
        try
        {
            ReportPath = ErrorReport.Write(new ReportInput
            {
                Step = step, Message = message, Drive = drive, Card = card,
                NavigationPath = copy?.NavigationPath, ManifestPath = copy?.ManifestPath,
                Compat = compat, Exception = e,
            });
            Detail = $"A report was saved to {ReportPath}";
        }
        catch (Exception re) when (re is IOException or UnauthorizedAccessException)
        {
            Detail = "The error report couldn't be saved: " + re.Message;
        }
        _summary = $"OpenLion {ErrorReport.AppVersion}: {step} failed.\n{message}\n" +
                   $"Card: {card?.DisplayName ?? drive.Label}" +
                   (compat is { Failed.Count: > 0 } ? $"\nTables: {string.Join(", ", compat.Failed)}" : "");
    }

    static void Open(string url) => Process.Start(new ProcessStartInfo(url) { UseShellExecute = true });
}
