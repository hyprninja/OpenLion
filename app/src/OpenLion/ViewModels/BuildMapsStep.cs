using System.Diagnostics;
using System.IO;
using System.Windows.Input;
using OpenLion.Core;
using OpenLion.Core.Engine;
using OpenLion.Core.Flow;
using OpenLion.Core.Osm;
using OpenLion.Core.Reports;
using OpenLion.Core.Speed;

namespace OpenLion.ViewModels;

public enum BuildPhase { Ready, Building, Done, Failed, Cancelled, Blocked }

/// <summary>Step 6: run the engine (openlion.make_card) to turn the OSM extract and the factory
/// copy into a finished, checked navigation/ folder.</summary>
public sealed class BuildMapsStep : StepViewModel
{
    /// <summary>Free space the engine needs: cached OSM reads (~1.3 GB), new tables, the card folder and,
    /// on PCs short of memory, osmium's node-location file for one read at a time (up to ~2.2 GB).</summary>
    const long NeededBytes = 6_000_000_000;
    /// <summary>The engine's memory-saving plan fits in about 4 GB (report 20), so an 8 GB PC works.</summary>
    const long MinimumMemory = 8L << 30;
    /// <summary>From about here the engine runs reads and map stages side by side (resources.py).</summary>
    const long FastMemory = 16L << 30;

    static long Memory => GC.GetGCMemoryInfo().TotalAvailableMemoryBytes;
    static bool FastPc => Memory >= FastMemory && Environment.ProcessorCount >= 8;

    readonly Session _session;
    readonly IUiTimer _timer;
    readonly Stopwatch _clock = new(), _stageClock = new();
    CancellationTokenSource? _cts;
    BuildProgress? _progress;
    readonly StateMachine<BuildPhase> _flow = new(Flows.Build);
    double _percent;
    string _status = "", _detail = "", _elapsed = "", _summary = "", _intro = "", _warning = "", _cameraNote = "", _alertNote = "";
    string? _reportPath;

    public BuildMapsStep(Session session) : base("Build maps")
    {
        _session = session;
        _timer = Platform.CreateTimer(TimeSpan.FromSeconds(1), Tick);
        StartCommand = new RelayCommand(() => _ = RunAsync(), () => Phase is not BuildPhase.Building and not BuildPhase.Blocked);
        CancelCommand = new RelayCommand(() => _cts?.Cancel(), () => IsBusy);
        OpenIssueCommand = new RelayCommand(() => Process.Start(new ProcessStartInfo(ErrorReport.IssueUrl) { UseShellExecute = true }));
        ShowReportCommand = new RelayCommand(() => Platform.RevealFile(_reportPath!),
            () => _reportPath is not null);
        CopySummaryCommand = new RelayCommand(() => Platform.CopyText(_summary), () => _summary.Length > 0);
    }

    public ICommand StartCommand { get; }
    public ICommand CancelCommand { get; }
    public ICommand OpenIssueCommand { get; }
    public ICommand ShowReportCommand { get; }
    public ICommand CopySummaryCommand { get; }

    /// <summary>Where the step is in <see cref="Flows.Build"/>.</summary>
    public BuildPhase Phase
    {
        get => _flow.State;
        private set
        {
            if (!_flow.Move(value)) return;
            Raise(); Raise(nameof(IsBusy)); Raise(nameof(IsReady)); Raise(nameof(IsDone)); Raise(nameof(IsFailed));
            Raise(nameof(IsCancelled)); Raise(nameof(CanGoNext)); Raise(nameof(StartLabel)); Raise(nameof(IsIdleOrFailed));
            Platform.RequeryCommands();
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
    /// <summary>Which states' speed cameras are official and up to date, and which get OSM's guess.</summary>
    public string CameraNote { get => _cameraNote; private set { if (Set(ref _cameraNote, value)) Raise(nameof(HasCameraNote)); } }
    public bool HasCameraNote => CameraNote.Length > 0;
    /// <summary>Where school zones, level crossings and New Zealand's addresses and suburbs come from.</summary>
    public string AlertNote { get => _alertNote; private set { if (Set(ref _alertNote, value)) Raise(nameof(HasAlertNote)); } }
    public bool HasAlertNote => AlertNote.Length > 0;
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
        if (build is not null && osm is not null && build.Matches(_session.FactoryRecord, _session.BuildKey()))
        {
            _session.Build = build;
            _session.RequiredBytes = build.Bytes;
            Status = $"Maps are ready: built {build.BuiltUtc.ToLocalTime():d MMMM 'at' h:mm tt} from {_session.DescribeOsm()}{Extras}.";
            Detail = $"{build.Files:N0} files, {build.Bytes / 1e6:N0} MB, checked.";
            Warning = build.Partial ? $"This is a TEST build of a small area only, not all of {Places}." : "";
            CameraNote = build.CameraNote ?? "";
            AlertNote = build.AlertNote ?? "";
            Phase = BuildPhase.Done;
            return;
        }
        _session.Build = null;
        Status = Detail = "";
        CameraNote = SpeedCameras.Preview(_session.Countries);
        AlertNote = RoadAlerts.Preview(_session.Countries);
        Phase = BuildPhase.Ready;
        Intro = osm is null ? "" :
            $"OpenLion will build new maps of {Places} from {_session.DescribeOsm()}{Extras}, using your factory card's copy. " +
            $"This takes {Duration} on a fast {Os.Computer} (longer on slower ones). You can keep using the {Os.Computer}, " +
            (Os.IsMac ? "and OpenLion stops the Mac going to sleep until it's done (on a laptop, keep the lid open)."
                      : "and OpenLion stops Windows going to sleep until it's done.") +
            (_session.Countries.Count == 1
                ? $" Only {Places} will have streets and addresses; the other country on the factory card is left empty."
                : "");
        Warning = CheckResources();
        if (FactoryNavigation is null || !Directory.Exists(FactoryNavigation) || osm is null)
        {
            Warning = "Go back and finish copying the factory card and getting the OSM data first.";
            Phase = BuildPhase.Blocked;
        }
    }

    string Places => MapCountry.Describe(_session.Countries);

    /// <summary>", with speed limits from Victorian speed zones (...)" or "".</summary>
    /// <summary>", with extra speed limits from ... and house numbers from G-NAF August 2026", or "".</summary>
    string Extras
    {
        get
        {
            var parts = new List<string>();
            if (_session.SpeedZones.Count > 0)
                parts.Add("extra speed limits from " + _session.DescribeSpeedZones());
            if (_session.Gnaf is not null)
                parts.Add("house numbers from " + _session.DescribeGnaf());
            if (_session.Voice is not null)
                parts.Add(_session.DescribeVoice());
            return parts.Count == 0 ? "" : ", with " + string.Join(" and ", parts);
        }
    }

    /// <summary>Rough build time, by extract size and PC (Australia + New Zealand, 1.4 GB of extracts:
    /// 25 minutes on a 44-thread PC with 64 GB, about an hour and a half in the memory-saving plan).</summary>
    string Duration
    {
        get
        {
            var gb = (_session.Osm ?? []).Sum(e => e.Bytes) / 1e9;
            if (FastPc)
                return gb < 0.6 ? "10 to 15 minutes" : "half an hour or so";
            return gb < 0.6 ? "about half an hour" : gb < 1.2 ? "about an hour" : "an hour and a half or so";
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
                notes.Add($"Only {free / 1e9:N1} GB free on {Os.DiskName(root)}; the build needs about {NeededBytes / 1e9:N1} GB.");
        }
        catch (Exception e) when (e is IOException or ArgumentException) { }
        var mem = Memory;
        var gbMem = mem / (double)(1L << 30);
        if (mem < MinimumMemory - (512L << 20))     // "8 GB" PCs report a little under 8
            notes.Add($"This {Os.Computer} has {gbMem:N0} GB of memory. OpenLion needs about 8 GB; with less, the build may be " +
                      "very slow or run out of memory. Close other programs before you start.");
        else if (mem < FastMemory)
            notes.Add($"This {Os.Computer} has {gbMem:N0} GB of memory, so OpenLion builds in its memory-saving mode: one step " +
                      "at a time, which takes longer. Close other programs before you start.");
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
        if (_session.FactoryRecord is null)
        {
            // A build is tied to the factory card it came from; without one the write step would refuse it.
            Warning = "There's no checked copy of your factory card in this session. Go back to Factory card and " +
                      $"copy it, or choose the copy already on this {Os.Computer}.";
            AppLog.Write("build: not started, no factory card record");
            return;
        }
        _cts = new CancellationTokenSource();
        var ct = _cts.Token;
        ReportPath = null;
        _summary = "";
        _progress = null;
        _session.Build = null;
        Percent = 0;
        Status = "Starting the map engine…";
        Detail = "";
        CameraNote = "";
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
                        Directory.Delete(AppPaths.EnsureInside(d, AppPaths.Root), true);
            }, ct);
            _session.State.Build = null;
            _session.State.Save();

            var bbox = MapBuild.BboxFromEnvironment();
            var extracts = _session.Countries.Zip(osm, (c, e) => (c.Code, e.Path)).ToList();
            var zones = _session.SpeedZones.Select(z => z.Path).ToList();
            var req = new BuildRequest(extracts, nav, FactoryManifest is { } m && File.Exists(m) ? m : null,
                AppPaths.BuildWorkFolder, AppPaths.BuildOutput, bbox, zones, AppPaths.CamerasFolder, _session.Gnaf?.Path,
                _session.Voice?.Path, AppPaths.GovDataFolder);
            AppLog.Write($"build: engine {engine.Python} ({(engine.Bundled ? "bundled" : "dev")}), " +
                         $"extracts {string.Join(", ", extracts.Select(x => $"{x.Code}={x.Path}"))}, " +
                         $"factory {nav}{(bbox is null ? "" : ", bbox " + string.Join(" ", bbox))}" +
                         (zones.Count == 0 ? "" : ", speed zones " + string.Join(", ", zones)) +
                         (_session.Gnaf is { } g ? ", G-NAF " + g.Path : "") +
                         (_session.Voice is { } v ? ", voice " + v.Path : ""));
            var progress = new Progress<BuildProgress>(p =>
            {
                _progress = p;
                _stageClock.Restart();
                Status = p.Stage.Label + "…";
                Detail = $"Stage {p.Index + 1} of {p.Count}";
                AppLog.Write($"build: stage {p.Stage.Id} at {_clock.Elapsed:hh\\:mm\\:ss}");
                Tick();
            });
            var cameras = new Progress<IReadOnlyList<CameraState>>(states =>
            {
                CameraNote = SpeedCameras.Describe(states);
                AppLog.Write("build: speed cameras " + string.Join(", ", states.Select(s =>
                    $"{s.Code} {s.Source}{(s.Cameras is { } n ? " " + n : "")}{(s.DownloadFailed ? " (download failed)" : "")}")));
            });
            var govData = new Progress<GovDataReport>(r =>
            {
                var note = RoadAlerts.Describe(r);
                if (note.Length > 0)
                    AlertNote = note;
                AppLog.Write("build: official lists " + string.Join(", ", r.Sources.Select(s =>
                    $"{s.Id} {s.Download ?? "-"} {s.Fetched?.ToString("yyyy-MM-dd") ?? "none"} {s.Count}")));
            });
            result = await MapBuild.RunAsync(engine, req, progress, ct, cameras, govData);
            AppLog.Write($"build: ok={result.Ok}, exit {result.ExitCode}, {result.Files} files, {result.Bytes} bytes, " +
                         $"error {result.Error}");
            if (!result.Ok)
            {
                Fail(result.Error ?? "unknown error", result, null);
                return;
            }
            var record = new BuildRecord(result.CardPath ?? AppPaths.BuildOutput, _session.FactoryRecord?.VolumeSerial ?? 0,
                BuildRecord.Key(osm, _session.SpeedZones, _session.Gnaf, _session.Voice?.Pack.Id), osm.Min(e => e.DataDate), result.Files, result.Bytes, DateTime.UtcNow, bbox is not null,
                result.Cameras.Count > 0 ? SpeedCameras.Describe(result.Cameras) : null,
                result.GovData is { } gd && RoadAlerts.Describe(gd) is { Length: > 0 } an ? an : null);
            CameraNote = record.CameraNote ?? "";
            AlertNote = record.AlertNote ?? AlertNote;
            _session.State.Build = record;
            _session.State.Save();
            _session.Build = record;
            _session.RequiredBytes = record.Bytes;
            Percent = 100;
            Status = $"Maps are ready: built in {Format(_clock.Elapsed)} from {_session.DescribeOsm()}{Extras}.";
            Detail = $"{result.Files:N0} files, {result.Bytes / 1e6:N0} MB. {result.Replaced} map files replaced, " +
                     "everything else is identical to your factory card, and the new card passed its checks.";
            Warning = bbox is not null ? $"This is a TEST build of a small area only, not all of {Places}." : "";
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
                   $"OSM data: {_session.BuildKey() ?? "none"}\nMemory: {GC.GetGCMemoryInfo().TotalAvailableMemoryBytes / (double)(1L << 30):N0} GB";
    }
}
