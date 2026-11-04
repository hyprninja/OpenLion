using System.Diagnostics;
using System.IO;
using System.Windows;
using System.Windows.Input;
using System.Windows.Threading;
using OpenLion.Core;
using OpenLion.Core.Engine;
using OpenLion.Core.Reports;

namespace OpenLion.ViewModels;

public enum BuildPhase { Ready, Building, Done, Failed, Cancelled, Blocked }

/// <summary>Step 5: run the engine (openlion.make_card) to turn the OSM extract and the factory
/// copy into a finished, checked navigation/ folder.</summary>
public sealed class BuildMapsStep : StepViewModel
{
    /// <summary>Free space the engine needs: cached OSM reads (~1.7 GB), new tables and the card folder.</summary>
    const long NeededBytes = 3_500_000_000;
    const long RecommendedMemory = 32L << 30;

    readonly Session _session;
    readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromSeconds(1) };
    readonly Stopwatch _clock = new(), _stageClock = new();
    CancellationTokenSource? _cts;
    BuildProgress? _progress;
    BuildPhase _phase;
    double _percent;
    string _status = "", _detail = "", _elapsed = "", _summary = "", _intro = "", _warning = "";
    string? _reportPath;

    public BuildMapsStep(Session session) : base("Build maps")
    {
        _session = session;
        _timer.Tick += (_, _) => Tick();
        StartCommand = new RelayCommand(() => _ = RunAsync(), () => Phase is not BuildPhase.Building and not BuildPhase.Blocked);
        CancelCommand = new RelayCommand(() => _cts?.Cancel(), () => IsBusy);
        OpenIssueCommand = new RelayCommand(() => Process.Start(new ProcessStartInfo(ErrorReport.IssueUrl) { UseShellExecute = true }));
        ShowReportCommand = new RelayCommand(() => Process.Start("explorer.exe", $"/select,\"{_reportPath}\""),
            () => _reportPath is not null);
        CopySummaryCommand = new RelayCommand(() => Clipboard.SetText(_summary), () => _summary.Length > 0);
    }

    public ICommand StartCommand { get; }
    public ICommand CancelCommand { get; }
    public ICommand OpenIssueCommand { get; }
    public ICommand ShowReportCommand { get; }
    public ICommand CopySummaryCommand { get; }

    public BuildPhase Phase
    {
        get => _phase;
        private set
        {
            if (!Set(ref _phase, value)) return;
            Raise(nameof(IsBusy)); Raise(nameof(IsReady)); Raise(nameof(IsDone)); Raise(nameof(IsFailed));
            Raise(nameof(IsCancelled)); Raise(nameof(CanGoNext)); Raise(nameof(StartLabel)); Raise(nameof(IsIdleOrFailed));
            CommandManager.InvalidateRequerySuggested();
        }
    }

    public bool IsBusy => Phase == BuildPhase.Building;
    /// <summary>Not started (or stopped): show what will be built.</summary>
    public bool IsReady => Phase is BuildPhase.Ready or BuildPhase.Cancelled or BuildPhase.Blocked;
    public bool IsDone => Phase == BuildPhase.Done;
    /// <summary>Show the start / build again button.</summary>
    public bool IsIdleOrFailed => Phase is not BuildPhase.Building and not BuildPhase.Blocked;
    public bool IsFailed => Phase == BuildPhase.Failed;
    public bool IsCancelled => Phase == BuildPhase.Cancelled;
    public string StartLabel => Phase is BuildPhase.Done or BuildPhase.Failed or BuildPhase.Cancelled ? "Build again" : "Start building";

    public double Percent { get => _percent; private set => Set(ref _percent, value); }
    public string Status { get => _status; private set => Set(ref _status, value); }
    public string Detail { get => _detail; private set => Set(ref _detail, value); }
    public string Elapsed { get => _elapsed; private set => Set(ref _elapsed, value); }
    /// <summary>What will be built from what, shown before starting.</summary>
    public string Intro { get => _intro; private set => Set(ref _intro, value); }
    /// <summary>Low memory / disk space, or a test-area build.</summary>
    public string Warning { get => _warning; private set { if (Set(ref _warning, value)) Raise(nameof(HasWarning)); } }
    public bool HasWarning => Warning.Length > 0;
    public string? ReportPath { get => _reportPath; private set => Set(ref _reportPath, value); }

    public override bool CanGoNext => Phase == BuildPhase.Done;
    public override bool IsWorking => IsBusy;

    string? FactoryNavigation => _session.FactoryCopy?.NavigationPath ??
                                 (_session.State.FactoryCopyPath is { } p ? Path.Combine(p, "navigation") : null);
    string? FactoryManifest => _session.FactoryCopy?.ManifestPath ??
                               (_session.State.FactoryCopyPath is { } p ? Path.Combine(p, "manifest.tsv") : null);

    public override void OnEnter()
    {
        if (IsBusy)
            return;
        var osm = _session.Osm;
        var build = _session.State.Build;
        if (build is not null && build.Matches(_session.FactoryRecord, osm?.FileName))
        {
            _session.Build = build;
            _session.RequiredBytes = build.Bytes;
            Status = $"Maps are ready: built {build.BuiltUtc.ToLocalTime():d MMMM 'at' h:mm tt} from {osm!.Describe()}.";
            Detail = $"{build.Files:N0} files, {build.Bytes / 1e6:N0} MB, checked.";
            Warning = build.Partial ? "This is a TEST build of a small area only, not all of Australia." : "";
            Phase = BuildPhase.Done;
            return;
        }
        _session.Build = null;
        Status = Detail = "";
        Phase = BuildPhase.Ready;
        Intro = osm is null ? "" :
            $"OpenLion will build new maps from {osm.Describe()}, using your factory card's copy. " +
            "This takes about an hour on a fast PC (longer on slower ones). You can keep using the PC, " +
            "and OpenLion stops Windows going to sleep until it's done.";
        Warning = CheckResources();
        if (FactoryNavigation is null || !Directory.Exists(FactoryNavigation) || osm is null)
        {
            Warning = "Go back and finish copying the factory card and getting the OSM data first.";
            Phase = BuildPhase.Blocked;
        }
    }

    string CheckResources()
    {
        var notes = new List<string>();
        try
        {
            var root = Path.GetPathRoot(Path.GetFullPath(AppPaths.Root))!;
            var free = new DriveInfo(root).AvailableFreeSpace + ExistingBuildBytes();
            if (free < NeededBytes)
                notes.Add($"Only {free / 1e9:N1} GB free on {root.TrimEnd('\\')}; the build needs about {NeededBytes / 1e9:N1} GB.");
        }
        catch (Exception e) when (e is IOException or ArgumentException) { }
        var mem = GC.GetGCMemoryInfo().TotalAvailableMemoryBytes;
        if (mem < RecommendedMemory)
            notes.Add($"This PC has {mem / (double)(1L << 30):N0} GB of memory. The build has only been tested on a PC " +
                      "with 64 GB and may run out of memory with less than 32 GB. Close other programs before you start.");
        if (MapBuild.BboxFromEnvironment() is { } b)
            notes.Add($"OPENLION_BBOX is set: only the test area {string.Join(" ", b)} will be built.");
        return string.Join(Environment.NewLine + Environment.NewLine, notes);
    }

    static long ExistingBuildBytes()
    {
        var d = new DirectoryInfo(Path.Combine(AppPaths.Root, "build"));
        return d.Exists ? d.EnumerateFiles("*", SearchOption.AllDirectories).Sum(f => f.Length) : 0;
    }

    public override void OnLeave() => _cts?.Cancel();

    void Tick()
    {
        Elapsed = $"Running for {Format(_clock.Elapsed)}";
        if (_progress is { } p)
            Percent = 100 * p.Fraction(_stageClock.Elapsed);
    }

    static string Format(TimeSpan t) => t.TotalHours >= 1 ? $"{(int)t.TotalHours} h {t.Minutes} min" :
        t.TotalMinutes >= 1 ? $"{(int)t.TotalMinutes} min" : $"{t.Seconds} s";

    async Task RunAsync()
    {
        var osm = _session.Osm;
        var nav = FactoryNavigation;
        if (IsBusy || osm is null || nav is null)
            return;
        _cts = new CancellationTokenSource();
        var ct = _cts.Token;
        ReportPath = null;
        _summary = "";
        _progress = null;
        _session.Build = null;
        Percent = 0;
        Status = "Starting the map engine…";
        Detail = "";
        Warning = CheckResources();
        Phase = BuildPhase.Building;
        _clock.Restart();
        _stageClock.Restart();
        _timer.Start();
        Tick();
        using var awake = new KeepAwake();
        BuildResult? result = null;
        try
        {
            var engine = EngineLocator.Locate() ?? throw new InvalidOperationException(
                "The map engine (Python) wasn't found. Please reinstall OpenLion.");
            // Old output goes first: it belongs to OpenLion and is rebuilt every time.
            await Task.Run(() =>
            {
                foreach (var d in new[] { AppPaths.BuildOutput, AppPaths.BuildOutput + ".partial" })
                    if (Directory.Exists(d))
                        Directory.Delete(d, true);
            }, ct);
            _session.State.Build = null;
            _session.State.Save();

            var bbox = MapBuild.BboxFromEnvironment();
            var req = new BuildRequest(osm.Path, nav, FactoryManifest is { } m && File.Exists(m) ? m : null,
                AppPaths.BuildWorkFolder, AppPaths.BuildOutput, bbox);
            AppLog.Write($"build: engine {engine.Python} ({(engine.Bundled ? "bundled" : "dev")}), pbf {osm.Path}, " +
                         $"factory {nav}{(bbox is null ? "" : ", bbox " + string.Join(" ", bbox))}");
            var progress = new Progress<BuildProgress>(p =>
            {
                _progress = p;
                _stageClock.Restart();
                Status = p.Stage.Label + "…";
                Detail = $"Stage {p.Index + 1} of {p.Count}";
                AppLog.Write($"build: stage {p.Stage.Id} at {_clock.Elapsed:hh\\:mm\\:ss}");
                Tick();
            });
            result = await MapBuild.RunAsync(engine, req, progress, ct);
            AppLog.Write($"build: ok={result.Ok}, exit {result.ExitCode}, {result.Files} files, {result.Bytes} bytes, " +
                         $"error {result.Error}");
            if (!result.Ok)
            {
                Fail(result.Error ?? "unknown error", result, null);
                return;
            }
            var record = new BuildRecord(result.CardPath ?? AppPaths.BuildOutput, _session.FactoryRecord?.VolumeSerial ?? 0,
                osm.FileName, osm.DataDate, result.Files, result.Bytes, DateTime.UtcNow, bbox is not null);
            _session.State.Build = record;
            _session.State.Save();
            _session.Build = record;
            _session.RequiredBytes = record.Bytes;
            Percent = 100;
            Status = $"Maps are ready: built in {Format(_clock.Elapsed)} from {osm.Describe()}.";
            Detail = $"{result.Files:N0} files, {result.Bytes / 1e6:N0} MB. {result.Replaced} map files replaced, " +
                     "everything else is identical to your factory card, and the new card passed its checks.";
            Warning = bbox is not null ? "This is a TEST build of a small area only, not all of Australia." : "";
            Phase = BuildPhase.Done;
        }
        catch (OperationCanceledException)
        {
            AppLog.Write("build: cancelled");
            Status = "Stopped. Press Build again to start over; data already read is reused, so it'll be quicker.";
            Detail = "";
            Phase = BuildPhase.Cancelled;
        }
        catch (Exception e)
        {
            Fail(e.Message, result, e);
        }
        finally
        {
            _timer.Stop();
            _clock.Stop();
        }
    }

    void Fail(string message, BuildResult? result, Exception? e)
    {
        var stage = result?.FailedStage;
        AppLog.Write($"build failed{(stage is null ? "" : " during " + stage)}: {message}{(e is null ? "" : Environment.NewLine + e)}");
        Status = "The map build failed" + (stage is null ? ": " : $" at the stage “{stage}”: ") + message;
        Detail = "";
        Phase = BuildPhase.Failed;
        try
        {
            ReportPath = ErrorReport.Write(new ReportInput
            {
                Step = "build maps" + (stage is null ? "" : $" ({stage})"),
                Message = message,
                Drive = _session.FactoryDrive,
                Card = _session.FactoryCard,
                NavigationPath = FactoryNavigation,
                ManifestPath = FactoryManifest,
                EngineLog = result?.Log,
                Exception = e,
            });
            Detail = $"A report was saved to {ReportPath}";
        }
        catch (Exception re) when (re is IOException or UnauthorizedAccessException)
        {
            Detail = "The error report couldn't be saved: " + re.Message;
        }
        _summary = $"OpenLion {ErrorReport.AppVersion}: map build failed{(stage is null ? "" : " during " + stage)}.\n{message}\n" +
                   $"OSM data: {_session.Osm?.FileName}\nMemory: {GC.GetGCMemoryInfo().TotalAvailableMemoryBytes / (double)(1L << 30):N0} GB";
    }
}
