using System.Collections.ObjectModel;
using System.IO;
using System.Net.Http;
using System.Windows.Input;
using OpenLion.Core;
using OpenLion.Core.Engine;
using OpenLion.Core.Osm;
using OpenLion.Core.Reports;
using OpenLion.Core.Speed;

namespace OpenLion.ViewModels;

/// <summary>One state on the Speed data step: a checkbox, and what's on this PC and online.</summary>
public sealed class SpeedZoneRow : ObservableObject
{
    readonly SpeedDataStep _step;
    bool _checked;
    SpeedZoneData? _cached;
    SpeedZoneRemote? _remote;
    string _remoteText = "";

    public SpeedZoneRow(SpeedDataStep step, SpeedZoneSource source, HttpClient http, bool isChecked)
    {
        _step = step;
        Source = source;
        _checked = isChecked && source.Available;
        Downloader = source.Available ? new SpeedZoneDownloader(http, AppPaths.SpeedZonesFolderFor(source), source) : null;
    }

    public SpeedZoneSource Source { get; }
    public SpeedZoneDownloader? Downloader { get; }
    public string Title => Source.Title;
    public bool IsAvailable => Source.Available;

    public bool IsChecked
    {
        get => _checked;
        set
        {
            if (!IsAvailable || _step.IsBusy || !Set(ref _checked, value)) return;
            Raise(nameof(ShowDetails));
            _step.OnRowToggled(this);
        }
    }

    /// <summary>Show what's downloaded and what's online: only for a ticked state.</summary>
    public bool ShowDetails => IsChecked;

    public SpeedZoneData? Cached
    {
        get => _cached;
        set
        {
            if (!Set(ref _cached, value)) return;
            Raise(nameof(HasCached)); Raise(nameof(CachedText)); Raise(nameof(IsLatest));
        }
    }
    public bool HasCached => Cached is not null;
    public string CachedText => Cached?.Describe() ?? "Not downloaded yet.";

    public SpeedZoneRemote? Remote
    {
        get => _remote;
        set { _remote = value; Raise(nameof(IsLatest)); }
    }
    public bool IsLatest => Remote is not null && SpeedZoneDownloader.IsSame(Cached, Remote);
    /// <summary>Ticked and needs a download: nothing here yet, or a newer month online.</summary>
    public bool WantsDownload => IsChecked && Remote is not null && !IsLatest;

    public string RemoteText { get => _remoteText; set => Set(ref _remoteText, value); }

    /// <summary>The note beside a state that can't be chosen yet.</summary>
    public string Note => IsAvailable
        ? $"{Source.Blurb} About {Source.ApproxMb} MB to download."
        : "Coming in a future version.";
}

/// <summary>Step 5: optional government speed zone data per state, for roads OpenStreetMap has no
/// speed limit for. Only Victoria publishes data OpenLion can use so far.</summary>
public sealed class SpeedDataStep : StepViewModel
{
    readonly Session _session;
    readonly HttpClient _http;
    CancellationTokenSource? _cts;
    bool _busy, _checking;
    double? _percent;
    string _status = "", _detail = "", _error = "";

    public SpeedDataStep(Session session) : base("Speed data")
    {
        _session = session;
        _http = OsmDownloader.CreateClient(ErrorReport.AppVersion);
        var saved = (session.State.SpeedZones ?? []).ToHashSet(StringComparer.OrdinalIgnoreCase);
        Rows = [.. SpeedZoneSource.All.Select(s => new SpeedZoneRow(this, s, _http, saved.Contains(s.Code)))];
        DownloadCommand = new RelayCommand(() => _ = DownloadAsync(), () => !IsBusy && Rows.Any(r => r.WantsDownload));
        CheckCommand = new RelayCommand(() => _ = CheckAsync(), () => !IsBusy && !_checking && Rows.Any(r => r.IsChecked));
        CancelCommand = new RelayCommand(() => _cts?.Cancel(), () => IsBusy);
    }

    public ObservableCollection<SpeedZoneRow> Rows { get; }
    /// <summary>The states that can be ticked, each in its own box.</summary>
    public IEnumerable<SpeedZoneRow> AvailableRows => Rows.Where(r => r.IsAvailable);
    /// <summary>The states still to come, listed together in one compact box.</summary>
    public IEnumerable<SpeedZoneRow> LaterRows => Rows.Where(r => !r.IsAvailable);
    public ICommand DownloadCommand { get; }
    public ICommand CheckCommand { get; }
    public ICommand CancelCommand { get; }

    /// <summary>Speed zone data is Australian; with only New Zealand on the card there's nothing to choose.</summary>
    public bool AustraliaOnCard => _session.Countries.Contains(MapCountry.Australia);
    public bool NoAustralia => !AustraliaOnCard;

    public bool IsBusy
    {
        get => _busy;
        private set { if (Set(ref _busy, value)) { Raise(nameof(CanGoNext)); Raise(nameof(IsIdle)); } }
    }
    public bool IsIdle => !IsBusy;
    public bool IsIndeterminate => _percent is null;
    public double Percent => _percent ?? 0;
    public string Status { get => _status; private set => Set(ref _status, value); }
    public string Detail { get => _detail; private set => Set(ref _detail, value); }
    public string Error { get => _error; private set { if (Set(ref _error, value)) Raise(nameof(HasError)); } }
    public bool HasError => Error.Length > 0;

    public bool ShowDownload => Rows.Any(r => r.WantsDownload);
    public string DownloadLabel => Rows.Where(r => r.WantsDownload).All(r => !r.HasCached) ? "Download" : "Download the newer data";

    /// <summary>"Victoria and Western Australia are available now. The other states ..."</summary>
    public string AvailableNote
    {
        get
        {
            var t = Rows.Where(r => r.IsAvailable).Select(r => r.Title).ToList();
            var names = t.Count <= 1 ? string.Concat(t) : string.Join(", ", t.Take(t.Count - 1)) + " and " + t[^1];
            return $"{names} {(t.Count == 1 ? "is" : "are")} available now. The other states and territories are coming soon, " +
                   "in future versions of OpenLion.";
        }
    }

    /// <summary>Credit lines for the chosen states (their licences ask for it).</summary>
    public string Credits => string.Join(Environment.NewLine,
        Rows.Where(r => r.IsAvailable).Select(r => r.Source.Credit));

    /// <summary>Optional: carry on with nothing ticked, but a ticked state must be downloaded first.</summary>
    public override bool CanGoNext => !IsBusy && (!AustraliaOnCard || Rows.Where(r => r.IsChecked).All(r => r.HasCached));
    public override bool IsWorking => IsBusy;

    void SetPercent(double? p)
    {
        _percent = p;
        Raise(nameof(Percent));
        Raise(nameof(IsIndeterminate));
    }

    void Publish()
    {
        _session.SpeedZones = AustraliaOnCard
            ? [.. Rows.Where(r => r.IsChecked && r.Cached is not null).Select(r => r.Cached!)]
            : [];
        Raise(nameof(CanGoNext)); Raise(nameof(ShowDownload)); Raise(nameof(DownloadLabel));
        CommandManager.InvalidateRequerySuggested();
    }

    internal void OnRowToggled(SpeedZoneRow row)
    {
        _session.State.SpeedZones = [.. Rows.Where(r => r.IsChecked).Select(r => r.Source.Code)];
        try { _session.State.Save(); }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException) { AppLog.Write("speed: state not saved: " + e.Message); }
        AppLog.Write("speed: chosen " + string.Join(",", _session.State.SpeedZones));
        Error = "";
        Publish();
        if (row.IsChecked && row.Remote is null)
            _ = CheckAsync();
    }

    public override void OnEnter()
    {
        Raise(nameof(AustraliaOnCard)); Raise(nameof(NoAustralia));
        foreach (var r in Rows)
            r.Cached = r.Downloader?.Cached();
        Publish();
        if (AustraliaOnCard && Rows.Any(r => r.IsChecked && r.Remote is null))
            _ = CheckAsync();
    }

    public override void OnLeave() => _cts?.Cancel();

    async Task CheckAsync()
    {
        if (_checking)
            return;
        _checking = true;
        Error = "";
        var done = new HashSet<SpeedZoneRow>();
        try
        {
            // a state ticked while another is being checked joins this pass (CheckAsync was a no-op for it)
            while (Rows.FirstOrDefault(r => r.IsChecked && r.Downloader is not null && !done.Contains(r)) is { } row)
            {
                done.Add(row);
                row.RemoteText = $"Checking the {row.Title} government data catalogue for the latest data…";
                try
                {
                    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(30));
                    row.Remote = await row.Downloader!.CheckLatestAsync(cts.Token);
                    AppLog.Write($"speed: {row.Source.Code} latest is {row.Remote.FileName} ({row.Remote.Name})");
                    row.RemoteText = row.IsLatest
                        ? $"This is the latest data ({row.Remote.Name})."
                        : $"Latest online: {row.Remote.Name}.";
                }
                catch (Exception e) when (e is HttpRequestException or TaskCanceledException or SpeedZoneException
                                              or System.Text.Json.JsonException)
                {
                    AppLog.Write($"speed: {row.Source.Code} check failed: " + e.Message);
                    row.Remote = null;
                    row.RemoteText = $"Couldn't reach the {row.Title} data catalogue ({row.Source.PackageApi?.Host}). " +
                                     "Check your internet connection and press Check again.";
                }
            }
        }
        finally
        {
            _checking = false;
            Publish();
        }
    }

    async Task DownloadAsync()
    {
        if (IsBusy)
            return;
        var engine = EngineLocator.Locate();
        if (engine is null)
        {
            Error = "The map engine (Python) wasn't found. Please reinstall OpenLion.";
            return;
        }
        _cts = new CancellationTokenSource();
        IsBusy = true;
        Error = "";
        try
        {
            foreach (var row in Rows.Where(r => r.WantsDownload).ToList())
            {
                var remote = row.Remote!;
                Status = $"Downloading {row.Title} speed data: {remote.Name}…";
                Detail = "";
                SetPercent(null);
                bool sections = row.Source.EngineFetch is not null;   // the engine counts road sections, not bytes
                var progress = new Progress<DownloadProgress>(p =>
                {
                    if (p.Phase == DownloadPhase.Verifying)
                    {
                        Status = $"Preparing the {row.Title} speed data{(sections ? "" : " (about a minute)")}…";
                        Detail = "";
                        SetPercent(null);
                        return;
                    }
                    SetPercent(p.Fraction * 100);
                    Detail = sections
                        ? $"{p.Done:N0} road sections"
                        : $"{p.Done / 1e6:N0}{(p.Total is { } t ? $" of {t / 1e6:N0}" : "")} MB" +
                          (p.BytesPerSecond > 0 ? $"  ({p.BytesPerSecond / 1e6:N1} MB/s)" : "");
                });
                row.Cached = await row.Downloader!.DownloadAsync(remote, engine, progress, _cts.Token);
                row.RemoteText = $"This is the latest data ({remote.Name}).";
                AppLog.Write($"speed: {row.Source.Code} ready {row.Cached.Path} ({row.Cached.Bytes} bytes) from {remote.FileName}");
            }
        }
        catch (OperationCanceledException)
        {
            Error = "Stopped. A download picks up where it left off next time.";
        }
        catch (Exception e) when (e is HttpRequestException or IOException or SpeedZoneException or UnauthorizedAccessException)
        {
            AppLog.Write("speed: download failed: " + e);
            Error = e is HttpRequestException ? "The download failed: " + e.Message + " Press Download to carry on." : e.Message;
        }
        finally
        {
            IsBusy = false;
            Status = "";
            Detail = "";
            Publish();
        }
    }
}
