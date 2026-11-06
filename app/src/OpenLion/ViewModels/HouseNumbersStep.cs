using System.IO;
using System.Net.Http;
using System.Windows.Input;
using Microsoft.Win32;
using OpenLion.Core;
using OpenLion.Core.Addresses;
using OpenLion.Core.Osm;
using OpenLion.Core.Reports;

namespace OpenLion.ViewModels;

/// <summary>Optional step: house numbers from G-NAF, the Australian government's address file, for
/// streets OpenStreetMap has none on. Only for cards with Australia on them.</summary>
public sealed class HouseNumbersStep : StepViewModel
{
    readonly Session _session;
    readonly GnafDownloader _downloader;
    CancellationTokenSource? _cts;
    bool _busy, _checking, _wanted;
    double? _percent;
    GnafData? _cached;
    GnafRemote? _remote;
    string _status = "", _detail = "", _error = "", _remoteText = "";

    public HouseNumbersStep(Session session) : base("House numbers")
    {
        _session = session;
        _downloader = new GnafDownloader(OsmDownloader.CreateClient(ErrorReport.AppVersion), AppPaths.GnafFolder);
        _wanted = session.State.HouseNumbers ?? true;
        DownloadCommand = new RelayCommand(() => _ = DownloadAsync(), () => !IsBusy && WantsDownload);
        CheckCommand = new RelayCommand(() => _ = CheckAsync(), () => !IsBusy && !_checking && Wanted);
        ImportCommand = new RelayCommand(() => _ = ImportAsync(), () => !IsBusy && Wanted);
        CancelCommand = new RelayCommand(() => _cts?.Cancel(), () => IsBusy);
    }

    public ICommand DownloadCommand { get; }
    public ICommand CheckCommand { get; }
    public ICommand ImportCommand { get; }
    public ICommand CancelCommand { get; }

    /// <summary>G-NAF is Australian; with only New Zealand on the card there's nothing to add.</summary>
    public bool AustraliaOnCard => _session.Countries.Contains(MapCountry.Australia);
    public bool NoAustralia => !AustraliaOnCard;

    /// <summary>The tick box: add house numbers from G-NAF.</summary>
    public bool Wanted
    {
        get => _wanted;
        set
        {
            if (IsBusy || !Set(ref _wanted, value)) return;
            _session.State.HouseNumbers = value;
            try { _session.State.Save(); }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { AppLog.Write("gnaf: state not saved: " + e.Message); }
            AppLog.Write("gnaf: wanted " + value);
            Error = "";
            Publish();
            if (value && _remote is null)
                _ = CheckAsync();
        }
    }

    public GnafData? Cached
    {
        get => _cached;
        private set
        {
            if (!Set(ref _cached, value)) return;
            Raise(nameof(HasCached)); Raise(nameof(CachedText));
        }
    }
    public bool HasCached => Cached is not null;
    public string CachedText => Cached is { } c ? c.Describe() + "." : "Not downloaded yet.";
    public string RemoteText { get => _remoteText; private set => Set(ref _remoteText, value); }

    bool IsLatest => _remote is not null && GnafDownloader.IsSame(Cached, _remote);
    /// <summary>Ticked and needs a download: nothing here yet, or a newer release online.</summary>
    public bool WantsDownload => Wanted && AustraliaOnCard && _remote is not null && !IsLatest;
    public bool ShowDownload => WantsDownload;
    public string DownloadLabel => HasCached ? "Download the newer release" : $"Download (about {GnafSource.ApproxMb / 1000.0:N1} GB)";

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
    public string Credit => GnafSource.Credit;

    /// <summary>Optional: untick to carry on without, but a ticked box needs the file first.</summary>
    public override bool CanGoNext => !IsBusy && (!AustraliaOnCard || !Wanted || HasCached);
    public override bool IsWorking => IsBusy;

    void SetPercent(double? p)
    {
        _percent = p;
        Raise(nameof(Percent));
        Raise(nameof(IsIndeterminate));
    }

    void Publish()
    {
        _session.Gnaf = AustraliaOnCard && Wanted ? Cached : null;
        Raise(nameof(CanGoNext)); Raise(nameof(ShowDownload)); Raise(nameof(WantsDownload)); Raise(nameof(DownloadLabel));
        CommandManager.InvalidateRequerySuggested();
    }

    public override void OnEnter()
    {
        Raise(nameof(AustraliaOnCard)); Raise(nameof(NoAustralia));
        Cached = _downloader.Cached();
        Publish();
        if (AustraliaOnCard && Wanted && _remote is null)
            _ = CheckAsync();
    }

    public override void OnLeave()
    {
        _cts?.Cancel();
        Publish();
    }

    async Task CheckAsync()
    {
        if (_checking)
            return;
        _checking = true;
        Error = "";
        RemoteText = "Checking data.gov.au for the latest release…";
        try
        {
            using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(30));
            _remote = await _downloader.CheckLatestAsync(cts.Token);
            AppLog.Write($"gnaf: latest is {_remote.FileName} ({GnafDownloader.Describe(_remote.FileName)}, {_remote.Bytes} bytes)");
            RemoteText = IsLatest ? $"This is the latest release ({GnafDownloader.Describe(_remote.FileName)})."
                                  : $"Latest online: {GnafDownloader.Describe(_remote.FileName)}" + (_remote.Bytes is { } b ? $", {b / 1e9:N1} GB." : ".");
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException or GnafException
                                      or System.Text.Json.JsonException)
        {
            AppLog.Write("gnaf: check failed: " + e.Message);
            _remote = null;
            RemoteText = "Couldn't reach data.gov.au. Check your internet connection and press Check again" +
                         (HasCached ? ", or carry on with the release you have." : ".");
        }
        finally
        {
            _checking = false;
            Publish();
        }
    }

    async Task DownloadAsync()
    {
        if (IsBusy || _remote is not { } remote)
            return;
        var need = (remote.Bytes ?? GnafSource.ApproxMb * 1_000_000L) + 200_000_000;
        try
        {
            var root = Path.GetPathRoot(Path.GetFullPath(AppPaths.Root))!;
            var free = new DriveInfo(root).AvailableFreeSpace;
            if (free < need)
            {
                Error = $"Only {free / 1e9:N1} GB free on {root.TrimEnd('\\')}; the address file needs about {need / 1e9:N1} GB.";
                return;
            }
        }
        catch (Exception e) when (e is IOException or ArgumentException) { }
        await RunAsync($"Downloading {GnafDownloader.Describe(remote.FileName)}…", (p, ct) => _downloader.DownloadAsync(remote, p, ct));
    }

    async Task ImportAsync()
    {
        if (IsBusy)
            return;
        var dlg = new OpenFileDialog
        {
            Title = "Choose a G-NAF release (the GDA2020 zip from data.gov.au)",
            Filter = "G-NAF release (*.zip)|*.zip|All files|*.*",
        };
        if (dlg.ShowDialog() != true)
            return;
        var path = dlg.FileName;
        await RunAsync($"Copying {Path.GetFileName(path)}…", (p, ct) => _downloader.ImportAsync(path, p, ct));
    }

    async Task RunAsync(string status, Func<IProgress<DownloadProgress>, CancellationToken, Task<GnafData>> work)
    {
        _cts = new CancellationTokenSource();
        IsBusy = true;
        Error = "";
        Status = status;
        Detail = "";
        SetPercent(null);
        try
        {
            var progress = new Progress<DownloadProgress>(p =>
            {
                if (p.Phase == DownloadPhase.Verifying)
                {
                    Status = "Checking the file…";
                    Detail = "";
                    SetPercent(null);
                    return;
                }
                SetPercent(p.Fraction * 100);
                Detail = $"{p.Done / 1e6:N0}{(p.Total is { } t ? $" of {t / 1e6:N0}" : "")} MB" +
                         (p.BytesPerSecond > 0 ? $"  ({p.BytesPerSecond / 1e6:N1} MB/s)" : "");
            });
            Cached = await work(progress, _cts.Token);
            AppLog.Write($"gnaf: ready {Cached.Path} ({Cached.Bytes} bytes, {Cached.Name})");
            RemoteText = _remote is not null && IsLatest ? $"This is the latest release ({GnafDownloader.Describe(_remote.FileName)})." : RemoteText;
        }
        catch (OperationCanceledException)
        {
            Error = "Stopped. A download picks up where it left off next time.";
        }
        catch (Exception e) when (e is HttpRequestException or IOException or GnafException or UnauthorizedAccessException)
        {
            AppLog.Write("gnaf: failed: " + e);
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
