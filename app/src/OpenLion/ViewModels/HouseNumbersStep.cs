using System.IO;
using System.Net.Http;
using System.Windows.Input;
using OpenLion.Core;
using OpenLion.Core.Addresses;
using OpenLion.Core.Osm;
using OpenLion.Core.Reports;

namespace OpenLion.ViewModels;

/// <summary>Optional step: house numbers from G-NAF, the Australian government's address file, for
/// streets OpenStreetMap has none on. Only for cards with Australia on them.</summary>
public sealed class HouseNumbersStep : ActivityStep
{
    readonly Session _session;
    readonly GnafDownloader _downloader;
    bool _wanted;
    GnafData? _cached;
    GnafRemote? _remote;
    string _remoteText = "";

    public HouseNumbersStep(Session session) : base("House numbers", "gnaf")
    {
        _session = session;
        _downloader = new GnafDownloader(OsmDownloader.CreateClient(ErrorReport.AppVersion), AppPaths.GnafFolder);
        _wanted = session.State.HouseNumbers ?? true;
        // Download waits for a running check: it needs the release the check is still fetching.
        DownloadCommand = new RelayCommand(() => _ = DownloadAsync(), () => !IsBusy && !IsChecking && WantsDownload);
        CheckCommand = new RelayCommand(() => _ = CheckAsync(), () => !IsBusy && !IsChecking && Wanted);
        ImportCommand = new RelayCommand(() => _ = ImportAsync(), () => !IsBusy && Wanted);
    }

    public ICommand DownloadCommand { get; }
    public ICommand CheckCommand { get; }
    public ICommand ImportCommand { get; }

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
            Activity.Dismiss();
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

    public string Credit => GnafSource.Credit;

    /// <summary>Optional: untick to carry on without, but a ticked box needs the file first.</summary>
    public override bool CanGoNext => !IsBusy && (!AustraliaOnCard || !Wanted || HasCached);

    protected override void OnActivityChanged() { Raise(nameof(ShowDownload)); Raise(nameof(WantsDownload)); }

    void Publish()
    {
        _session.Gnaf = AustraliaOnCard && Wanted ? Cached : null;
        Raise(nameof(CanGoNext)); Raise(nameof(ShowDownload)); Raise(nameof(WantsDownload)); Raise(nameof(DownloadLabel));
        Platform.RequeryCommands();
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
        Activity.Cancel();
        Publish();
    }

    async Task CheckAsync()
    {
        if (!Activity.BeginCheck())
            return;
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
            Activity.EndCheck();
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
                Activity.Fail($"Only {free / 1e9:N1} GB free on {Os.DiskName(root)}; the address file needs about {need / 1e9:N1} GB.");
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
        var path = await Platform.PickFileAsync("Choose a G-NAF release (the GDA2020 zip from data.gov.au)",
            "G-NAF release (*.zip)|*.zip|All files|*.*");
        if (path is null)
            return;
        await RunAsync($"Copying {Path.GetFileName(path)}…", (p, ct) => _downloader.ImportAsync(path, p, ct));
    }

    async Task RunAsync(string status, Func<IProgress<DownloadProgress>, CancellationToken, Task<GnafData>> work)
    {
        var ct = Activity.Begin(status);
        try
        {
            var progress = new Progress<DownloadProgress>(p =>
            {
                if (p.Phase == DownloadPhase.Verifying)
                    Activity.Report(null, "", "Checking the file…");
                else
                    Activity.Report(p.Fraction * 100, $"{p.Done / 1e6:N0}{(p.Total is { } t ? $" of {t / 1e6:N0}" : "")} MB" +
                                                      (p.BytesPerSecond > 0 ? $"  ({p.BytesPerSecond / 1e6:N1} MB/s)" : ""));
            });
            Cached = await work(progress, ct);
            AppLog.Write($"gnaf: ready {Cached.Path} ({Cached.Bytes} bytes, {Cached.Name})");
            RemoteText = _remote is not null && IsLatest ? $"This is the latest release ({GnafDownloader.Describe(_remote.FileName)})." : RemoteText;
            Activity.Finish();
        }
        catch (OperationCanceledException)
        {
            Activity.Stop("Stopped. A download picks up where it left off next time.");
        }
        catch (Exception e) when (e is HttpRequestException or IOException or GnafException or UnauthorizedAccessException)
        {
            AppLog.Write("gnaf: failed: " + e);
            Activity.Fail(e is HttpRequestException ? "The download failed: " + e.Message + " Press Download to carry on." : e.Message);
        }
        finally
        {
            Activity.Settle();
            Publish();
        }
    }
}
