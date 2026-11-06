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
public sealed record SpeedZoneSource(string Code, string Title, Uri? PackageApi, IReadOnlyList<string> AllowedHosts,
    string Credit, int ApproxMb)
{
    public static readonly SpeedZoneSource Victoria = new("VIC", "Victoria",
        new Uri("https://discover.data.vic.gov.au/api/3/action/package_show?id=speed-zones"),
        ["opendata.transport.vic.gov.au", "discover.data.vic.gov.au"],
        "Speed zone data © State of Victoria (Department of Transport and Planning), CC BY 4.0", 480);

    static SpeedZoneSource Later(string code, string title) => new(code, title, null, [], "", 0);

    /// <summary>Every Australian state and territory, Victoria first; only those with a
    /// <see cref="PackageApi"/> can be chosen.</summary>
    public static readonly IReadOnlyList<SpeedZoneSource> All =
    [
        Victoria,
        Later("NSW", "New South Wales"),
        Later("QLD", "Queensland"),
        Later("SA", "South Australia"),
        Later("WA", "Western Australia"),
        Later("TAS", "Tasmania"),
        Later("ACT", "Australian Capital Territory"),
        Later("NT", "Northern Territory"),
    ];

    public bool Available => PackageApi is not null;
    /// <summary>Speed zone data only goes on Australian maps.</summary>
    public MapCountry Country => MapCountry.Australia;

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
        return Newest(doc.RootElement, source);
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
                if (!string.Equals(Str("format"), "geojson", StringComparison.OrdinalIgnoreCase) ||
                    !Uri.TryCreate(Str("url"), UriKind.Absolute, out var url) || !Allowed(url, source))
                    continue;
                var when = Date(Str("last_modified")) ?? Date(Str("created"));
                var name = Path.GetFileName(url.AbsolutePath);
                if (name.Length == 0)
                    continue;
                if (best is null || (when ?? DateTime.MinValue) > (best.Modified ?? DateTime.MinValue))
                    best = new SpeedZoneRemote(url, name, Str("name") ?? name, when);
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
        var geo = Path.Combine(folder, remote.FileName);
        var part = geo + ".part";
        foreach (var stale in Directory.EnumerateFiles(folder, "*.part").Where(p => !p.Equals(part, StringComparison.OrdinalIgnoreCase)))
            File.Delete(stale);
        if (!File.Exists(geo))
        {
            await FetchAsync(remote.Url, part, progress, ct);
            File.Move(part, geo, overwrite: true);
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
