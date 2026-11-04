using System.IO;
using System.Net.Http;
using System.Windows.Input;
using Microsoft.Win32;
using OpenLion.Core;
using OpenLion.Core.Osm;
using OpenLion.Core.Reports;

namespace OpenLion.ViewModels;

/// <summary>Step 4: get the Australia OpenStreetMap extract from Geofabrik (or a file the user has).</summary>
public sealed class GetOsmStep : StepViewModel
{
    readonly Session _session;
    readonly OsmDownloader _downloader;
    CancellationTokenSource? _cts;
    OsmExtract? _cached;
    OsmRemote? _remote;
    bool _busy, _checking;
    double? _percent;
    string _status = "", _detail = "", _error = "", _remoteText = "";

    public GetOsmStep(Session session) : base("Get OSM data")
    {
        _session = session;
        _downloader = new OsmDownloader(OsmDownloader.CreateClient(ErrorReport.AppVersion), AppPaths.OsmFolder);
        DownloadCommand = new RelayCommand(() => _ = DownloadAsync(), () => !IsBusy && _remote is not null && !IsLatest);
        ImportCommand = new RelayCommand(() => _ = ImportAsync(), () => !IsBusy);
        CheckCommand = new RelayCommand(() => _ = CheckAsync(), () => !IsBusy && !_checking);
        CancelCommand = new RelayCommand(() => _cts?.Cancel(), () => IsBusy);
    }

    public ICommand DownloadCommand { get; }
    public ICommand ImportCommand { get; }
    public ICommand CheckCommand { get; }
    public ICommand CancelCommand { get; }

    public bool IsBusy
    {
        get => _busy;
        private set { if (Set(ref _busy, value)) { Raise(nameof(CanGoNext)); Raise(nameof(IsIdle)); } }
    }
    public bool IsIdle => !IsBusy;
    public bool IsChecking { get => _checking; private set => Set(ref _checking, value); }
    public bool IsIndeterminate => _percent is null;
    public double Percent => _percent ?? 0;
    public string Status { get => _status; private set => Set(ref _status, value); }
    public string Detail { get => _detail; private set => Set(ref _detail, value); }
    public string Error { get => _error; private set { if (Set(ref _error, value)) Raise(nameof(HasError)); } }
    public bool HasError => Error.Length > 0;

    public OsmExtract? Cached
    {
        get => _cached;
        private set
        {
            if (!Set(ref _cached, value)) return;
            _session.Osm = value;
            Raise(nameof(HasCached)); Raise(nameof(CachedText)); Raise(nameof(CanGoNext)); Raise(nameof(IsLatest));
            Raise(nameof(ShowDownload));
        }
    }
    public bool HasCached => Cached is not null;
    public string CachedText => Cached?.Describe() ?? "";

    /// <summary>What Geofabrik has, once checked.</summary>
    public string RemoteText { get => _remoteText; private set => Set(ref _remoteText, value); }
    public bool IsLatest => _remote is not null && OsmDownloader.IsSame(Cached, _remote);
    public string DownloadLabel => Cached is null ? "Download" : "Download the newer data";
    /// <summary>Hide the download button once the extract on this PC is the latest.</summary>
    public bool ShowDownload => !IsLatest;

    public override bool CanGoNext => Cached is not null && !IsBusy;
    public override bool IsWorking => IsBusy;

    void SetPercent(double? p)
    {
        _percent = p;
        Raise(nameof(Percent));
        Raise(nameof(IsIndeterminate));
    }

    public override void OnEnter()
    {
        Cached = _downloader.Cached();
        if (_remote is null)
            _ = CheckAsync();
    }

    public override void OnLeave() => _cts?.Cancel();

    async Task CheckAsync()
    {
        IsChecking = true;
        Error = "";
        RemoteText = "Checking Geofabrik for the latest data…";
        try
        {
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            _remote = await _downloader.CheckLatestAsync(cts.Token);
            AppLog.Write($"osm: latest is {_remote.FileName}, {_remote.Bytes} bytes, md5 {_remote.Md5}");
            var size = _remote.Bytes is { } b ? $", {b / 1e6:N0} MB" : "";
            var date = _remote.DataDate is { } d ? d.ToString("d MMMM yyyy") : _remote.FileName;
            RemoteText = IsLatest
                ? $"This is the latest data on Geofabrik ({date})."
                : $"Latest on Geofabrik: data from {date}{size}.";
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException or OsmDownloadException)
        {
            AppLog.Write("osm: check failed: " + e.Message);
            _remote = null;
            RemoteText = "Couldn't reach Geofabrik (download.geofabrik.de). Check your internet connection and press Check again.";
        }
        finally
        {
            IsChecking = false;
            Raise(nameof(IsLatest));
            Raise(nameof(ShowDownload));
            Raise(nameof(DownloadLabel));
            CommandManager.InvalidateRequerySuggested();
        }
    }

    async Task DownloadAsync()
    {
        if (_remote is not { } remote || IsBusy)
            return;
        await RunAsync($"Downloading {remote.FileName} from Geofabrik…",
            (p, ct) => _downloader.DownloadAsync(remote, p, ct));
    }

    async Task ImportAsync()
    {
        var dlg = new OpenFileDialog
        {
            Title = "Choose an OpenStreetMap extract for Australia",
            Filter = "OpenStreetMap extract (*.osm.pbf)|*.osm.pbf|All files|*.*",
        };
        if (dlg.ShowDialog() != true)
            return;
        var path = dlg.FileName;
        await RunAsync($"Copying {Path.GetFileName(path)}…", (p, ct) => _downloader.ImportAsync(path, p, ct));
    }

    async Task RunAsync(string status, Func<IProgress<DownloadProgress>, CancellationToken, Task<OsmExtract>> work)
    {
        _cts = new CancellationTokenSource();
        IsBusy = true;
        Error = "";
        Status = status;
        Detail = "";
        SetPercent(null);
        var progress = new Progress<DownloadProgress>(p =>
        {
            SetPercent(p.Fraction * 100);
            var mb = $"{p.Done / 1e6:N0}{(p.Total is { } t ? $" of {t / 1e6:N0}" : "")} MB";
            Detail = p.Phase switch
            {
                DownloadPhase.Verifying => $"Checking the file: {mb}",
                DownloadPhase.Copying => mb,
                _ => mb + (p.BytesPerSecond > 0 ? $"  ({p.BytesPerSecond / 1e6:N1} MB/s{Eta(p)})" : ""),
            };
            if (p.Phase == DownloadPhase.Verifying)
                Status = "Checking the download…";
        });
        try
        {
            Cached = await work(progress, _cts.Token);
            AppLog.Write($"osm: ready {Cached.FileName} ({Cached.Bytes} bytes, md5 {Cached.Md5 ?? "not checked"})");
            Raise(nameof(IsLatest));
            if (_remote is not null && IsLatest)
                RemoteText = "This is the latest data on Geofabrik.";
        }
        catch (OperationCanceledException)
        {
            Error = "Stopped. A download picks up where it left off next time.";
        }
        catch (Exception e) when (e is HttpRequestException or IOException or OsmDownloadException or UnauthorizedAccessException)
        {
            AppLog.Write("osm: failed: " + e);
            Error = e is HttpRequestException
                ? "The download failed: " + e.Message + " Press Download to carry on from where it stopped."
                : e.Message;
        }
        finally
        {
            IsBusy = false;
            Status = "";
            Detail = "";
            Raise(nameof(DownloadLabel));
            CommandManager.InvalidateRequerySuggested();
        }
    }

    static string Eta(DownloadProgress p)
    {
        if (p.Total is not { } t || p.BytesPerSecond <= 0)
            return "";
        var s = (t - p.Done) / p.BytesPerSecond;
        return s < 90 ? ", under 2 minutes left" : $", about {Math.Ceiling(s / 60):N0} minutes left";
    }
}
