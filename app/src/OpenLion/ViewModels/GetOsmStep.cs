using System.Collections.ObjectModel;
using System.IO;
using System.Net.Http;
using System.Windows.Input;
using Microsoft.Win32;
using OpenLion.Core;
using OpenLion.Core.Osm;
using OpenLion.Core.Reports;

namespace OpenLion.ViewModels;

/// <summary>One way to fill the card: Australia, New Zealand, or both (the factory card had both).</summary>
public sealed record CountryChoice(string Label, string Note, IReadOnlyList<MapCountry> Countries);

/// <summary>One country's OpenStreetMap extract: what's on this PC and what Geofabrik has.</summary>
public sealed class CountryOsm : ObservableObject
{
    readonly GetOsmStep _step;
    OsmExtract? _cached;
    OsmRemote? _remote;
    string _remoteText = "";

    public CountryOsm(GetOsmStep step, MapCountry country, HttpClient http)
    {
        _step = step;
        Country = country;
        Downloader = new OsmDownloader(http, AppPaths.OsmFolderFor(country), country);
        ImportCommand = new RelayCommand(() => _ = step.ImportAsync(this), () => !step.IsBusy);
    }

    public MapCountry Country { get; }
    public OsmDownloader Downloader { get; }
    public ICommand ImportCommand { get; }
    public string Title => Country.Title;

    public OsmExtract? Cached
    {
        get => _cached;
        set
        {
            if (!Set(ref _cached, value)) return;
            Raise(nameof(HasCached)); Raise(nameof(CachedText)); Raise(nameof(IsLatest));
        }
    }
    public bool HasCached => Cached is not null;
    public string CachedText => Cached?.Describe() ?? "Nothing downloaded yet.";

    public OsmRemote? Remote
    {
        get => _remote;
        set { _remote = value; Raise(nameof(IsLatest)); }
    }
    public bool IsLatest => Remote is not null && OsmDownloader.IsSame(Cached, Remote);
    /// <summary>Needs a download: there is newer data, or nothing here yet.</summary>
    public bool WantsDownload => Remote is not null && !IsLatest;

    /// <summary>What Geofabrik has, once checked.</summary>
    public string RemoteText { get => _remoteText; set => Set(ref _remoteText, value); }
}

/// <summary>Step 4: choose the countries, then get each one's OpenStreetMap extract from Geofabrik
/// (or a file the user has).</summary>
public sealed class GetOsmStep : StepViewModel
{
    readonly Session _session;
    readonly HttpClient _http;
    readonly Dictionary<string, CountryOsm> _all = [];
    CancellationTokenSource? _cts;
    CountryChoice _choice;
    bool _busy, _checking;
    double? _percent;
    string _status = "", _detail = "", _error = "";

    public GetOsmStep(Session session) : base("Get OSM data")
    {
        _session = session;
        _http = OsmDownloader.CreateClient(ErrorReport.AppVersion);
        foreach (var c in MapCountry.All)
            _all[c.Code] = new CountryOsm(this, c, _http);
        Choices =
        [
            new("Australia", "about 1 GB to download", [MapCountry.Australia]),
            new("New Zealand", "about 400 MB to download", [MapCountry.NewZealand]),
            new("Australia and New Zealand", "both, like the factory card; about 1.4 GB", [MapCountry.Australia, MapCountry.NewZealand]),
        ];
        var saved = MapCountry.FromCodes(session.State.Countries);
        _choice = Choices.FirstOrDefault(ch => ch.Countries.SequenceEqual(saved)) ?? Choices[0];
        session.Countries = _choice.Countries;
        Rows = [.. _choice.Countries.Select(c => _all[c.Code])];
        DownloadCommand = new RelayCommand(() => _ = DownloadAsync(), () => !IsBusy && Rows.Any(r => r.WantsDownload));
        CheckCommand = new RelayCommand(() => _ = CheckAsync(), () => !IsBusy && !_checking);
        CancelCommand = new RelayCommand(() => _cts?.Cancel(), () => IsBusy);
    }

    public IReadOnlyList<CountryChoice> Choices { get; }
    /// <summary>The selected countries' extracts, in card order.</summary>
    public ObservableCollection<CountryOsm> Rows { get; }

    public ICommand DownloadCommand { get; }
    public ICommand CheckCommand { get; }
    public ICommand CancelCommand { get; }

    public CountryChoice Choice
    {
        get => _choice;
        set
        {
            if (value is null || IsBusy || !Set(ref _choice, value)) return;
            Rows.Clear();
            foreach (var c in value.Countries)
                Rows.Add(_all[c.Code]);
            _session.Countries = value.Countries;
            _session.State.Countries = [.. value.Countries.Select(c => c.Code)];
            try { _session.State.Save(); }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { AppLog.Write("osm: state not saved: " + e.Message); }
            AppLog.Write("osm: countries " + string.Join(",", _session.State.Countries));
            Error = "";
            Publish();
            if (Rows.Any(r => r.Remote is null))
                _ = CheckAsync();
        }
    }

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

    public bool AllCached => Rows.All(r => r.HasCached);
    public bool ShowDownload => Rows.Any(r => r.WantsDownload);
    public string DownloadLabel => Rows.Where(r => r.WantsDownload).ToList() is var w && w.All(r => !r.HasCached)
        ? "Download"
        : "Download the newer data";

    public override bool CanGoNext => AllCached && !IsBusy;
    public override bool IsWorking => IsBusy;

    void SetPercent(double? p)
    {
        _percent = p;
        Raise(nameof(Percent));
        Raise(nameof(IsIndeterminate));
    }

    /// <summary>Hands the selected extracts to the session and refreshes what depends on them.</summary>
    void Publish()
    {
        _session.Countries = _choice.Countries;
        _session.Osm = AllCached ? [.. Rows.Select(r => r.Cached!)] : null;
        Raise(nameof(AllCached)); Raise(nameof(CanGoNext)); Raise(nameof(ShowDownload)); Raise(nameof(DownloadLabel));
        CommandManager.InvalidateRequerySuggested();
    }

    public override void OnEnter()
    {
        foreach (var r in _all.Values)
            r.Cached = r.Downloader.Cached();
        Publish();
        if (Rows.Any(r => r.Remote is null))
            _ = CheckAsync();
    }

    public override void OnLeave() => _cts?.Cancel();

    async Task CheckAsync()
    {
        if (IsChecking)
            return;
        IsChecking = true;
        Error = "";
        try
        {
            foreach (var row in Rows.ToList())
            {
                row.RemoteText = "Checking Geofabrik for the latest data…";
                try
                {
                    using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(30));
                    row.Remote = await row.Downloader.CheckLatestAsync(cts.Token);
                    var rm = row.Remote;
                    AppLog.Write($"osm: {row.Country.Code} latest is {rm.FileName}, {rm.Bytes} bytes, md5 {rm.Md5}");
                    var size = rm.Bytes is { } b ? $", {b / 1e6:N0} MB" : "";
                    var date = rm.DataDate is { } d ? d.ToString("d MMMM yyyy") : rm.FileName;
                    row.RemoteText = row.IsLatest
                        ? $"This is the latest data on Geofabrik ({date})."
                        : $"Latest on Geofabrik: data from {date}{size}.";
                }
                catch (Exception e) when (e is HttpRequestException or TaskCanceledException or OsmDownloadException)
                {
                    AppLog.Write($"osm: {row.Country.Code} check failed: " + e.Message);
                    row.Remote = null;
                    row.RemoteText = "Couldn't reach Geofabrik (download.geofabrik.de). Check your internet connection and press Check again.";
                }
            }
        }
        finally
        {
            IsChecking = false;
            Publish();
        }
    }

    /// <summary>Downloads every selected country that has nothing here yet or newer data on Geofabrik.</summary>
    async Task DownloadAsync()
    {
        if (IsBusy)
            return;
        foreach (var row in Rows.Where(r => r.WantsDownload).ToList())
        {
            var remote = row.Remote!;
            var ok = await RunAsync(row, $"Downloading {row.Title}: {remote.FileName} from Geofabrik…",
                (p, ct) => row.Downloader.DownloadAsync(remote, p, ct));
            if (!ok)
                break;
        }
    }

    internal async Task ImportAsync(CountryOsm row)
    {
        if (IsBusy)
            return;
        var dlg = new OpenFileDialog
        {
            Title = $"Choose an OpenStreetMap extract for {row.Title}",
            Filter = "OpenStreetMap extract (*.osm.pbf)|*.osm.pbf|All files|*.*",
        };
        if (dlg.ShowDialog() != true)
            return;
        var path = dlg.FileName;
        await RunAsync(row, $"Copying {Path.GetFileName(path)}…", (p, ct) => row.Downloader.ImportAsync(path, p, ct));
    }

    async Task<bool> RunAsync(CountryOsm row, string status,
        Func<IProgress<DownloadProgress>, CancellationToken, Task<OsmExtract>> work)
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
                Status = $"Checking the {row.Title} download…";
        });
        try
        {
            row.Cached = await work(progress, _cts.Token);
            AppLog.Write($"osm: {row.Country.Code} ready {row.Cached.FileName} ({row.Cached.Bytes} bytes, md5 {row.Cached.Md5 ?? "not checked"})");
            if (row.IsLatest)
                row.RemoteText = "This is the latest data on Geofabrik.";
            return true;
        }
        catch (OperationCanceledException)
        {
            Error = "Stopped. A download picks up where it left off next time.";
        }
        catch (Exception e) when (e is HttpRequestException or IOException or OsmDownloadException or UnauthorizedAccessException)
        {
            AppLog.Write($"osm: {row.Country.Code} failed: " + e);
            Error = e is HttpRequestException
                ? $"The {row.Title} download failed: " + e.Message + " Press Download to carry on from where it stopped."
                : e.Message;
        }
        finally
        {
            IsBusy = false;
            Status = "";
            Detail = "";
            Publish();
        }
        return false;
    }

    static string Eta(DownloadProgress p)
    {
        if (p.Total is not { } t || p.BytesPerSecond <= 0)
            return "";
        var s = (t - p.Done) / p.BytesPerSecond;
        return s < 90 ? ", under 2 minutes left" : $", about {Math.Ceiling(s / 60):N0} minutes left";
    }
}
