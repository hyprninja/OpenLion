using System.Globalization;
using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;
using OpenLion.Core.Engine;
using OpenLion.Core.Osm;

namespace OpenLion.Core.Speed;

/// <summary>A state or territory and its open speed zone data, if OpenLion can use it
/// (see openlion/gov_speed.py and docs/21).</summary>
/// <param name="Code">Short code, also the folder name ("VIC").</param>
/// <param name="PackageApi">CKAN package_show URL that lists the dataset's files; null when not available yet.</param>
/// <param name="AllowedHosts">Hosts a data file may come from (the catalogue's links are only followed to these).</param>
/// <param name="Credit">Attribution the licence asks for.</param>
/// <param name="Blurb">What the data covers, shown beside the checkbox.</param>
/// <param name="EngineFetch">The engine command that downloads the data (gov_speed.py "fetch-wa"),
/// for data served in pages by a map server rather than as one file; null when the app downloads
/// the newest file listed in the catalogue itself.</param>
/// <param name="CountryCode">The map country the data belongs to (MapCountry code): the source is
/// only offered when that country is on the card.</param>
/// <param name="DataName">The data's name in its version ("Legal Speed Limits 5 November 2026"), for
/// sources whose catalogue has no file names; null takes the catalogue's own title.</param>
public sealed record SpeedZoneSource(string Code, string Title, Uri? PackageApi, IReadOnlyList<string> AllowedHosts,
    string Credit, int ApproxMb, string Blurb = "", string? EngineFetch = null, string CountryCode = "AU",
    string? DataName = null)
{
    public static readonly SpeedZoneSource Victoria = new("VIC", "Victoria",
        new Uri("https://discover.data.vic.gov.au/api/3/action/package_show?id=speed-zones"),
        ["opendata.transport.vic.gov.au", "discover.data.vic.gov.au"],
        "Speed zone data © State of Victoria (Department of Transport and Planning), CC BY 4.0", 480,
        "Every road in the state, updated monthly by the state government.");

    public static readonly SpeedZoneSource WesternAustralia = new("WA", "Western Australia",
        new Uri("https://catalogue.data.wa.gov.au/api/3/action/package_show?id=mrwa-legal-speed-limits"),
        ["catalogue.data.wa.gov.au", "gisservices.mainroads.wa.gov.au"],
        "Speed limit data © Main Roads Western Australia, CC BY 4.0", 16,
        "Signposted limits on state and many local roads, updated weekly by Main Roads WA.",
        "fetch-wa", DataName: "Legal Speed Limits");

    /// <summary>NZ's National Speed Limit Register: the legal register itself, every road in the country.
    /// Its version is the ArcGIS layer's last edit date (<see cref="PackageApi"/> is the layer's JSON).</summary>
    public static readonly SpeedZoneSource NewZealand = new("NZ", "New Zealand",
        new Uri("https://services.arcgis.com/CXBb7LAjgIIdcsPt/ArcGIS/rest/services/SpeedLimitZoneFull__View/FeatureServer/0?f=json"),
        ["services.arcgis.com"],
        "Speed limit data © NZ Transport Agency Waka Kotahi (National Speed Limit Register), CC BY 4.0", 180,
        "The legal speed limit register for every road, kept up to date by NZTA.",
        "fetch-nz", "NZ", "National Speed Limit Register");

    /// <summary>Transport for NSW's speed zone register. The file keeps one name, so its version is
    /// the catalogue's date for it ("Speed Zones 6 November 2026").</summary>
    public static readonly SpeedZoneSource NewSouthWales = new("NSW", "New South Wales",
        new Uri("https://opendata.transport.nsw.gov.au/data/api/3/action/package_show?id=speed-zones"),
        ["opendata.transport.nsw.gov.au"],
        "Speed zone data © Transport for NSW, CC BY", 375,
        "Every road in the state, from Transport for NSW's speed zone register.",
        DataName: "Speed Zones");

    static SpeedZoneSource Later(string code, string title) => new(code, title, null, [], "", 0);

    /// <summary>The states and territories that publish (or may soon publish) usable open data, the
    /// ones OpenLion can use first, then New Zealand; only those with a <see cref="PackageApi"/> can be
    /// chosen, and only for the countries on the card. South
    /// Australia and the Northern Territory are left off: they don't publish their speed limits in
    /// an open format (see <see cref="NotOpen"/>).</summary>
    public static readonly IReadOnlyList<SpeedZoneSource> All =
    [
        Victoria,
        WesternAustralia,
        NewSouthWales,
        Later("QLD", "Queensland"),
        Later("TAS", "Tasmania"),
        Later("ACT", "Australian Capital Territory"),
        NewZealand,
    ];

    /// <summary>States and territories with no open speed limit data, named in a note on the page.</summary>
    public static readonly IReadOnlyList<string> NotOpen = ["South Australia", "the Northern Territory"];

    public bool Available => PackageApi is not null;
    /// <summary>The map country this data is for.</summary>
    public MapCountry Country => MapCountry.ByCode(CountryCode) ?? MapCountry.Australia;

    public static SpeedZoneSource? ByCode(string? code) =>
        All.FirstOrDefault(s => string.Equals(s.Code, code, StringComparison.OrdinalIgnoreCase));
}

/// <summary>Prepared speed zones on this PC: the compact file the engine reads.</summary>
/// <param name="Name">The dataset file's title ("Speed Zones September 2026").</param>
/// <param name="SourceFile">The file it was made from (its name changes every month).</param>
public sealed record SpeedZoneData(string Code, string Path, string FileName, string Name, string SourceFile,
    long Bytes, DateTime? Modified, DateTime AddedUtc)
{
    public string Describe() => $"{Name}, ready to use";
}

/// <summary>The newest data file in the dataset.</summary>
public sealed record SpeedZoneRemote(Uri Url, string FileName, string Name, DateTime? Modified);

public class SpeedZoneException(string message, Exception? inner = null) : Exception(message, inner);

/// <summary>
/// Gets one state's speed zones: asks the data catalogue (CKAN) for the newest GeoJSON, downloads
/// it, has the engine turn it into its compact file (python -m openlion.gov_speed prepare), and
/// keeps only that (about 20 MB instead of about 480 MB).
/// </summary>
public sealed class SpeedZoneDownloader(HttpClient http, string folder, SpeedZoneSource source)
{
    const string InfoFile = "zones.json";
    const string Prepared = "zones.bin";
    static readonly JsonSerializerOptions Json = new() { WriteIndented = true };

    public SpeedZoneSource Source => source;
    public string Folder => folder;

    /// <summary>The prepared data on this PC, or null.</summary>
    public SpeedZoneData? Cached()
    {
        try
        {
            var info = Path.Combine(folder, InfoFile);
            if (!File.Exists(info))
                return null;
            var d = JsonSerializer.Deserialize<SpeedZoneData>(File.ReadAllText(info), Json);
            if (d is null || d.Code != source.Code)
                return null;
            var path = Path.Combine(folder, Prepared);
            var fi = new FileInfo(path);
            return fi.Exists && fi.Length == d.Bytes ? d with { Path = path } : null;
        }
        catch (Exception x) when (x is IOException or JsonException or UnauthorizedAccessException)
        {
            return null;
        }
    }

    /// <summary>Same file as the newest in the dataset.</summary>
    public static bool IsSame(SpeedZoneData? cached, SpeedZoneRemote remote) =>
        cached is not null && string.Equals(cached.SourceFile, remote.FileName, StringComparison.OrdinalIgnoreCase);

    public async Task<SpeedZoneRemote> CheckLatestAsync(CancellationToken ct = default)
    {
        if (source.PackageApi is not { } api)
            throw new SpeedZoneException($"There's no speed zone data for {source.Title} yet.");
        using var r = await http.GetAsync(api, ct);
        r.EnsureSuccessStatusCode();
        await using var s = await r.Content.ReadAsStreamAsync(ct);
        using var doc = await JsonDocument.ParseAsync(s, cancellationToken: ct);
        return source.EngineFetch is null ? Newest(doc.RootElement, source) : Dated(doc.RootElement, source);
    }

    /// <summary>For data the engine fetches: the catalogue's last-updated date is the version
    /// ("wa-2026-11-05", "Legal Speed Limits 5 November 2026"). Public for tests.</summary>
    public static SpeedZoneRemote Dated(JsonElement reply, SpeedZoneSource source)
    {
        DateTime? when = null;
        string? title = null;
        try
        {
            if (reply.TryGetProperty("result", out var r))        // CKAN package_show
            {
                if (r.TryGetProperty("metadata_modified", out var m) && m.ValueKind == JsonValueKind.String)
                    when = Date(m.GetString());
                if (r.TryGetProperty("title", out var t) && t.ValueKind == JsonValueKind.String)
                    title = t.GetString();
            }
            else if (reply.TryGetProperty("editingInfo", out var ed) && ed.ValueKind == JsonValueKind.Object)   // ArcGIS layer
            {
                foreach (var f in new[] { "dataLastEditDate", "lastEditDate" })
                    if (when is null && ed.TryGetProperty(f, out var ms) && ms.ValueKind == JsonValueKind.Number)
                        when = DateTimeOffset.FromUnixTimeMilliseconds(ms.GetInt64()).UtcDateTime;
            }
        }
        catch (Exception e) when (e is KeyNotFoundException or InvalidOperationException or ArgumentOutOfRangeException)
        {
            throw new SpeedZoneException($"The {source.Title} data catalogue sent an answer OpenLion doesn't understand.", e);
        }
        if (when is null)
            throw new SpeedZoneException($"The {source.Title} data catalogue gave no date for its speed limits.");
        title = source.DataName ?? title;
        var name = $"{(string.IsNullOrWhiteSpace(title) ? "Speed limits" : title)} {when.Value.ToString("d MMMM yyyy", CultureInfo.InvariantCulture)}";
        return new SpeedZoneRemote(source.PackageApi!, $"{source.Code.ToLowerInvariant()}-{when.Value:yyyy-MM-dd}", name, when);
    }

    /// <summary>The newest GeoJSON resource in a CKAN package_show reply. Public for tests.</summary>
    public static SpeedZoneRemote Newest(JsonElement reply, SpeedZoneSource source)
    {
        SpeedZoneRemote? best = null;
        try
        {
            foreach (var res in reply.GetProperty("result").GetProperty("resources").EnumerateArray())
            {
                string? Str(string n) => res.TryGetProperty(n, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
                if (!Uri.TryCreate(Str("url"), UriKind.Absolute, out var url) || !Allowed(url, source))
                    continue;
                var name = Path.GetFileName(url.AbsolutePath);
                // NSW labels its GeoJSON "data": go by the file name too
                if (name.Length == 0 || !(string.Equals(Str("format"), "geojson", StringComparison.OrdinalIgnoreCase) ||
                                          name.EndsWith(".geojson", StringComparison.OrdinalIgnoreCase)))
                    continue;
                var when = Date(Str("last_modified")) ?? Date(Str("created"));
                var title = Str("name") ?? name;
                if (source.DataName is { } dn && when is { } w)
                {
                    // one file name for every version: the catalogue's date tells them apart
                    name = $"{Path.GetFileNameWithoutExtension(name)}_{w:yyyy-MM-dd}{Path.GetExtension(name)}";
                    title = $"{dn} {w.ToString("d MMMM yyyy", CultureInfo.InvariantCulture)}";
                }
                if (best is null || (when ?? DateTime.MinValue) > (best.Modified ?? DateTime.MinValue))
                    best = new SpeedZoneRemote(url, name, title, when);
            }
        }
        catch (Exception e) when (e is KeyNotFoundException or InvalidOperationException)
        {
            throw new SpeedZoneException($"The {source.Title} data catalogue sent an answer OpenLion doesn't understand.", e);
        }
        return best ?? throw new SpeedZoneException($"The {source.Title} data catalogue lists no GeoJSON file.");
    }

    static bool Allowed(Uri url, SpeedZoneSource source) =>
        url.Scheme == Uri.UriSchemeHttps && source.AllowedHosts.Contains(url.Host, StringComparer.OrdinalIgnoreCase);

    static DateTime? Date(string? s) =>
        DateTime.TryParse(s, CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out var d)
            ? d : null;

    /// <summary>Downloads the file (resuming a stopped download), prepares it with the engine and
    /// keeps the prepared file.</summary>
    public async Task<SpeedZoneData> DownloadAsync(SpeedZoneRemote remote, EnginePaths engine,
        IProgress<DownloadProgress>? progress = null, CancellationToken ct = default)
    {
        if (!Allowed(remote.Url, source))
            throw new SpeedZoneException($"Refusing to download from {remote.Url.Host}.");
        Directory.CreateDirectory(folder);
        string geo;
        if (source.EngineFetch is { } cmd)
            geo = await EngineFetchAsync(engine, cmd, progress, ct);
        else
        {
            geo = Path.Combine(folder, remote.FileName);
            var part = geo + ".part";
            foreach (var stale in Directory.EnumerateFiles(folder, "*.part").Where(p => !p.Equals(part, StringComparison.OrdinalIgnoreCase)))
                File.Delete(stale);
            if (!File.Exists(geo))
            {
                await FetchAsync(remote.Url, part, progress, ct);
                File.Move(part, geo, overwrite: true);
            }
        }

        progress?.Report(new DownloadProgress(DownloadPhase.Verifying, 0, null, 0));
        var bin = Path.Combine(folder, Prepared + ".new");
        var log = new List<string>();
        int code = await EngineRunner.RunAsync(engine, ["-m", "openlion.gov_speed", "prepare", geo, bin],
            line => { lock (log) log.Add(line); }, ct);
        if (code != 0 || !File.Exists(bin))
        {
            File.Delete(geo);   // most likely a damaged download: fetch it again next time
            string tail;
            lock (log) tail = string.Join(" ", log.Where(l => l.StartsWith(EngineRunner.StderrPrefix)).TakeLast(2)
                .Select(l => l[EngineRunner.StderrPrefix.Length..]));
            throw new SpeedZoneException($"The {source.Title} speed zone file couldn't be read (exit code {code}). " +
                                         "Press Download to try again. " + tail);
        }
        var final = Path.Combine(folder, Prepared);
        File.Move(bin, final, overwrite: true);
        File.Delete(geo);
        var data = new SpeedZoneData(source.Code, final, Prepared, remote.Name, remote.FileName,
            new FileInfo(final).Length, remote.Modified, DateTime.UtcNow);
        var info = Path.Combine(folder, InfoFile);
        File.WriteAllText(info + ".tmp", JsonSerializer.Serialize(data, Json));
        File.Move(info + ".tmp", info, overwrite: true);
        return data;
    }

    /// <summary>Has the engine download the data (python -m openlion.gov_speed fetch-wa folder): it
    /// prints "progress n" while it pages through the map server, then the data's name and the
    /// file it wrote. Returns that file, which must be in this source's folder.</summary>
    async Task<string> EngineFetchAsync(EnginePaths engine, string cmd, IProgress<DownloadProgress>? progress,
        CancellationToken ct)
    {
        var lines = new List<string>();
        int code = await EngineRunner.RunAsync(engine, ["-m", "openlion.gov_speed", cmd, folder], line =>
        {
            lock (lines) lines.Add(line);
            if (line.StartsWith("progress ", StringComparison.Ordinal) && long.TryParse(line[9..], out var n))
                progress?.Report(new DownloadProgress(DownloadPhase.Downloading, n, null, 0));
        }, ct);
        List<string> output;
        lock (lines) output = [.. lines.Where(l => !l.StartsWith(EngineRunner.StderrPrefix) && !l.StartsWith("progress "))];
        string? path = output.Count > 0 ? output[^1].Trim() : null;
        if (code != 0 || path is null)
        {
            string tail;
            lock (lines) tail = string.Join(" ", lines.Where(l => l.StartsWith(EngineRunner.StderrPrefix)).TakeLast(2)
                .Select(l => l[EngineRunner.StderrPrefix.Length..]));
            throw new SpeedZoneException($"The {source.Title} speed limits couldn't be downloaded (exit code {code}). " +
                                         "Check your internet connection and press Download to try again. " + tail);
        }
        var full = Path.GetFullPath(path);
        if (!full.StartsWith(Path.GetFullPath(folder).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar,
                StringComparison.OrdinalIgnoreCase) || !File.Exists(full))
            throw new SpeedZoneException($"The map engine wrote the {source.Title} speed limits somewhere unexpected: {full}");
        return full;
    }

    async Task FetchAsync(Uri url, string part, IProgress<DownloadProgress>? progress, CancellationToken ct)
    {
        long have = File.Exists(part) ? new FileInfo(part).Length : 0;
        using var req = new HttpRequestMessage(HttpMethod.Get, url);
        if (have > 0)
            req.Headers.Range = new RangeHeaderValue(have, null);
        using var r = await http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct);
        if (r.StatusCode == HttpStatusCode.RequestedRangeNotSatisfiable && have > 0)
            return;     // already complete
        r.EnsureSuccessStatusCode();
        bool resume = have > 0 && r.StatusCode == HttpStatusCode.PartialContent;
        if (!resume)
            have = 0;
        long? total = r.Content.Headers.ContentLength is { } cl ? cl + have : null;
        await using var src = await r.Content.ReadAsStreamAsync(ct);
        await using var dst = new FileStream(part, resume ? FileMode.Append : FileMode.Create, FileAccess.Write,
            FileShare.None, 1 << 20, FileOptions.Asynchronous);
        var buf = new byte[1 << 20];
        long done = have, since = 0;
        var clock = System.Diagnostics.Stopwatch.StartNew();
        var tick = TimeSpan.Zero;
        double rate = 0;
        int n;
        while ((n = await src.ReadAsync(buf, ct)) > 0)
        {
            await dst.WriteAsync(buf.AsMemory(0, n), ct);
            done += n;
            since += n;
            var now = clock.Elapsed;
            if (now - tick >= TimeSpan.FromMilliseconds(250))
            {
                var inst = since / (now - tick).TotalSeconds;
                rate = rate == 0 ? inst : rate * 0.8 + inst * 0.2;
                tick = now;
                since = 0;
                progress?.Report(new DownloadProgress(DownloadPhase.Downloading, done, total, rate));
            }
        }
        progress?.Report(new DownloadProgress(DownloadPhase.Downloading, done, total, rate));
    }
}
