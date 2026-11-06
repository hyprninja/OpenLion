using System.Globalization;
using System.IO.Compression;
using System.Net;
using System.Net.Http.Headers;
using System.Text.Json;
using OpenLion.Core.Osm;

namespace OpenLion.Core.Addresses;

/// <summary>G-NAF, Australia's national address file (Geoscape, on data.gov.au, quarterly): house
/// numbers for streets OpenStreetMap has none on. The engine reads the zip as it is
/// (openlion/gov_addr.py, make_card --addresses; docs/23).</summary>
public static class GnafSource
{
    /// <summary>data.gov.au's catalogue entry (CKAN package_show) that lists the release files.</summary>
    public static readonly Uri PackageApi =
        new("https://data.gov.au/data/api/3/action/package_show?id=19432f89-dc3a-4ef3-b943-5326ef1dbecc");

    /// <summary>Hosts a release file may come from.</summary>
    public static readonly IReadOnlyList<string> AllowedHosts = ["data.gov.au", "www.data.gov.au"];

    public const string Credit =
        "Incorporates or developed using G-NAF © Geoscape Australia licensed by the Commonwealth of Australia " +
        "under the Open Geo-coded National Address File (G-NAF) End User Licence Agreement.";

    public const int ApproxMb = 1900;

    public static bool Allowed(Uri url) =>
        url.Scheme == Uri.UriSchemeHttps && AllowedHosts.Contains(url.Host, StringComparer.OrdinalIgnoreCase);
}

/// <summary>A G-NAF release on this PC.</summary>
/// <param name="Name">The release's title ("AUG 2026 - Geoscape G-NAF - GDA2020").</param>
public sealed record GnafData(string Path, string FileName, string Name, long Bytes, DateTime? Modified, DateTime AddedUtc)
{
    public string Describe() => $"{GnafDownloader.Describe(FileName)}, ready to use";
}

/// <summary>The newest GDA2020 release in the catalogue.</summary>
public sealed record GnafRemote(Uri Url, string FileName, string Name, long? Bytes, DateTime? Modified);

public class GnafException(string message, Exception? inner = null) : Exception(message, inner);

/// <summary>Finds, downloads (resuming a stopped download) and checks the G-NAF zip, keeping one
/// release in <paramref name="folder"/>.</summary>
public sealed class GnafDownloader(HttpClient http, string folder)
{
    const string InfoFile = "gnaf.json";
    static readonly JsonSerializerOptions Json = new() { WriteIndented = true };

    public string Folder => folder;

    /// <summary>The release on this PC, or null.</summary>
    public GnafData? Cached()
    {
        try
        {
            var info = Path.Combine(folder, InfoFile);
            if (!File.Exists(info))
                return null;
            var d = JsonSerializer.Deserialize<GnafData>(File.ReadAllText(info), Json);
            if (d is null || d.FileName != Path.GetFileName(d.FileName))
                return null;
            var path = Path.Combine(folder, d.FileName);
            var fi = new FileInfo(path);
            return fi.Exists && fi.Length == d.Bytes ? d with { Path = path } : null;
        }
        catch (Exception x) when (x is IOException or JsonException or UnauthorizedAccessException)
        {
            return null;
        }
    }

    public static bool IsSame(GnafData? cached, GnafRemote remote) =>
        cached is not null && string.Equals(cached.FileName, remote.FileName, StringComparison.OrdinalIgnoreCase);

    public async Task<GnafRemote> CheckLatestAsync(CancellationToken ct = default)
    {
        using var r = await http.GetAsync(GnafSource.PackageApi, ct);
        r.EnsureSuccessStatusCode();
        await using var s = await r.Content.ReadAsStreamAsync(ct);
        using var doc = await JsonDocument.ParseAsync(s, cancellationToken: ct);
        return Newest(doc.RootElement);
    }

    /// <summary>The newest GDA2020 zip in a CKAN package_show reply (GDA2020 matches the
    /// coordinates OpenStreetMap uses; GDA94 is ~1.8 m off). Public for tests.</summary>
    public static GnafRemote Newest(JsonElement reply)
    {
        GnafRemote? best = null;
        try
        {
            foreach (var res in reply.GetProperty("result").GetProperty("resources").EnumerateArray())
            {
                string? Str(string n) => res.TryGetProperty(n, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
                if (!Uri.TryCreate(Str("url"), UriKind.Absolute, out var url) || !GnafSource.Allowed(url))
                    continue;
                var file = Path.GetFileName(url.AbsolutePath);
                if (!string.Equals(Str("format"), "zip", StringComparison.OrdinalIgnoreCase) ||
                    !file.EndsWith(".zip", StringComparison.OrdinalIgnoreCase) ||
                    !file.Contains("gda2020", StringComparison.OrdinalIgnoreCase) || file != Path.GetFileName(file))
                    continue;
                long? size = res.TryGetProperty("size", out var sz) && sz.ValueKind == JsonValueKind.Number &&
                             sz.TryGetInt64(out var n) && n > 0 ? n : null;
                var when = Date(Str("last_modified")) ?? Date(Str("created"));
                if (best is null || (when ?? DateTime.MinValue) > (best.Modified ?? DateTime.MinValue))
                    best = new GnafRemote(url, file, Str("name") ?? file, size, when);
            }
        }
        catch (Exception e) when (e is KeyNotFoundException or InvalidOperationException)
        {
            throw new GnafException("data.gov.au sent an answer OpenLion doesn't understand.", e);
        }
        return best ?? throw new GnafException("data.gov.au lists no G-NAF release (GDA2020 zip) right now.");
    }

    static DateTime? Date(string? s) =>
        DateTime.TryParse(s, CultureInfo.InvariantCulture, DateTimeStyles.AdjustToUniversal | DateTimeStyles.AssumeUniversal, out var d)
            ? d : null;

    /// <summary>Is this a G-NAF release? Its zip holds the states' ADDRESS_DETAIL tables. Public for tests.</summary>
    public static bool LooksLikeGnaf(string zipPath)
    {
        try
        {
            using var z = ZipFile.OpenRead(zipPath);
            return z.Entries.Any(e => e.FullName.Contains("/Standard/", StringComparison.Ordinal) &&
                                      e.Name.EndsWith("_ADDRESS_DETAIL_psv.psv", StringComparison.Ordinal));
        }
        catch (Exception e) when (e is InvalidDataException or IOException or UnauthorizedAccessException)
        {
            return false;
        }
    }

    /// <summary>Downloads the release, checks its size and contents, and keeps it as the one release here.</summary>
    public async Task<GnafData> DownloadAsync(GnafRemote remote, IProgress<DownloadProgress>? progress = null,
        CancellationToken ct = default)
    {
        if (!GnafSource.Allowed(remote.Url))
            throw new GnafException($"Refusing to download from {remote.Url.Host}.");
        Directory.CreateDirectory(folder);
        var final = Path.Combine(folder, remote.FileName);
        var part = final + ".part";
        foreach (var stale in Directory.EnumerateFiles(folder, "*.part").Where(p => !p.Equals(part, StringComparison.OrdinalIgnoreCase)))
            File.Delete(stale);
        await FetchAsync(remote.Url, part, progress, ct);
        progress?.Report(new DownloadProgress(DownloadPhase.Verifying, 0, null, 0));
        var size = new FileInfo(part).Length;
        if (remote.Bytes is { } want && size != want)
        {
            File.Delete(part);
            throw new GnafException($"The download is {size:N0} bytes but data.gov.au lists {want:N0}. Press Download to try again.");
        }
        if (!await Task.Run(() => LooksLikeGnaf(part), ct))
        {
            File.Delete(part);
            throw new GnafException("The downloaded file isn't a readable G-NAF release. Press Download to try again.");
        }
        File.Move(part, final, overwrite: true);
        return Keep(final, remote.Name, remote.Modified);
    }

    /// <summary>Copies a G-NAF zip the user already has (checked first) and keeps it as the release here.</summary>
    public async Task<GnafData> ImportAsync(string source, IProgress<DownloadProgress>? progress = null, CancellationToken ct = default)
    {
        progress?.Report(new DownloadProgress(DownloadPhase.Verifying, 0, null, 0));
        if (!await Task.Run(() => LooksLikeGnaf(source), ct))
            throw new GnafException($"{Path.GetFileName(source)} isn't a G-NAF release (no ADDRESS_DETAIL tables in it).");
        Directory.CreateDirectory(folder);
        var name = Path.GetFileName(source);
        var final = Path.Combine(folder, name);
        if (!string.Equals(Path.GetFullPath(source), Path.GetFullPath(final), StringComparison.OrdinalIgnoreCase))
        {
            var part = final + ".part";
            long total = new FileInfo(source).Length, done = 0;
            await using (var src = new FileStream(source, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20, FileOptions.Asynchronous))
            await using (var dst = new FileStream(part, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 20, FileOptions.Asynchronous))
            {
                var buf = new byte[1 << 20];
                int n;
                while ((n = await src.ReadAsync(buf, ct)) > 0)
                {
                    await dst.WriteAsync(buf.AsMemory(0, n), ct);
                    done += n;
                    progress?.Report(new DownloadProgress(DownloadPhase.Copying, done, total, 0));
                }
            }
            File.Move(part, final, overwrite: true);
        }
        return Keep(final, Describe(name), File.GetLastWriteTimeUtc(final));
    }

    /// <summary>"g-naf_aug26_allstates_gda2020_psv_110.zip" -> "G-NAF August 2026".</summary>
    public static string Describe(string fileName)
    {
        var m = System.Text.RegularExpressions.Regex.Match(fileName, @"g-naf_([a-z]{3})(\d{2})_", System.Text.RegularExpressions.RegexOptions.IgnoreCase);
        if (m.Success && DateTime.TryParseExact(m.Groups[1].Value + m.Groups[2].Value, "MMMyy", CultureInfo.InvariantCulture,
                DateTimeStyles.None, out var d))
            return $"G-NAF {d:MMMM yyyy}";
        return Path.GetFileNameWithoutExtension(fileName);
    }

    GnafData Keep(string path, string name, DateTime? modified)
    {
        var data = new GnafData(path, Path.GetFileName(path), name, new FileInfo(path).Length, modified, DateTime.UtcNow);
        var info = Path.Combine(folder, InfoFile);
        File.WriteAllText(info + ".tmp", JsonSerializer.Serialize(data, Json));
        File.Move(info + ".tmp", info, overwrite: true);
        // one release at a time: each is ~1.9 GB
        foreach (var old in Directory.EnumerateFiles(folder, "*.zip")
                     .Where(p => !p.Equals(path, StringComparison.OrdinalIgnoreCase)))
        {
            try { File.Delete(old); }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException) { AppLog.Write("gnaf: couldn't delete " + old + ": " + e.Message); }
        }
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
        if (r.RequestMessage?.RequestUri is { } final && !GnafSource.Allowed(final))
            throw new GnafException($"The download was redirected to {final.Host}; OpenLion only downloads G-NAF from data.gov.au.");
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
