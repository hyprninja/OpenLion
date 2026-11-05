using System.Globalization;
using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace OpenLion.Core.Osm;

/// <summary>An OSM extract on this PC.</summary>
/// <param name="DataDate">Date of the OSM data, from Geofabrik's file name; null if unknown.</param>
/// <param name="Md5">Checked against Geofabrik's checksum; null for a file the user supplied.</param>
public sealed record OsmExtract(string Path, string FileName, DateOnly? DataDate, long Bytes, string? Md5,
    DateTime AddedUtc, string Source)
{
    public string Describe() =>
        $"{(DataDate is { } d ? "OpenStreetMap data from " + d.ToString("d MMMM yyyy", CultureInfo.CurrentCulture) : "OpenStreetMap data (date unknown)")}, {Bytes / 1e6:N0} MB";
}

/// <summary>The newest extract on Geofabrik's server.</summary>
public sealed record OsmRemote(Uri Url, string FileName, DateOnly? DataDate, long? Bytes, string Md5);

public enum DownloadPhase { Downloading, Verifying, Copying }

public sealed record DownloadProgress(DownloadPhase Phase, long Done, long? Total, double BytesPerSecond)
{
    public double? Fraction => Total is > 0 ? Math.Min(1.0, (double)Done / Total.Value) : null;
}

public class OsmDownloadException(string message, Exception? inner = null) : Exception(message, inner);

/// <summary>
/// Downloads one country's extract from Geofabrik into its cache folder, keeping one extract
/// there. "-latest" redirects to a dated file (australia-261103.osm.pbf) that never changes, so
/// a download can safely resume from its .part file. Every download is checked against
/// Geofabrik's MD5 before it is used.
/// </summary>
public sealed class OsmDownloader(HttpClient http, string folder, MapCountry? country = null)
{
    /// <summary>Australia's "-latest" URL (OpenLion 1.0 only downloaded Australia).</summary>
    public static readonly Uri LatestUrl = MapCountry.Australia.LatestUrl;

    public MapCountry Country { get; } = country ?? MapCountry.Australia;
    const string InfoFile = "extract.json";
    const long SpaceMargin = 200_000_000;

    static readonly JsonSerializerOptions Json = new() { WriteIndented = true };
    static readonly Regex DatedName = new(@"-(\d{6})\.osm\.pbf$", RegexOptions.IgnoreCase);

    public string Folder => folder;

    /// <summary>A client with OpenLion's user agent and no overall timeout (downloads are long).</summary>
    public static HttpClient CreateClient(string version)
    {
        var c = new HttpClient(new SocketsHttpHandler { AutomaticDecompression = DecompressionMethods.None })
        {
            Timeout = Timeout.InfiniteTimeSpan,
        };
        c.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("OpenLion", version));
        c.DefaultRequestHeaders.UserAgent.Add(new ProductInfoHeaderValue("(+https://github.com/hyprninja/OpenLion)"));
        return c;
    }

    /// <summary>The extract already on this PC, or null.</summary>
    public OsmExtract? Cached()
    {
        try
        {
            var info = Path.Combine(folder, InfoFile);
            if (!File.Exists(info))
                return null;
            var e = JsonSerializer.Deserialize<OsmExtract>(File.ReadAllText(info), Json);
            if (e is null)
                return null;
            var path = Path.Combine(folder, e.FileName);
            var fi = new FileInfo(path);
            return fi.Exists && fi.Length == e.Bytes ? e with { Path = path } : null;
        }
        catch (Exception x) when (x is IOException or JsonException or UnauthorizedAccessException)
        {
            return null;
        }
    }

    /// <summary>Asks Geofabrik for the newest extract: its dated name, size and MD5.</summary>
    public async Task<OsmRemote> CheckLatestAsync(CancellationToken ct = default)
    {
        var latest = Country.LatestUrl;
        using var head = new HttpRequestMessage(HttpMethod.Head, latest);
        using var r = await http.SendAsync(head, ct);
        r.EnsureSuccessStatusCode();
        var url = r.RequestMessage?.RequestUri ?? latest; // after redirects
        var name = Path.GetFileName(url.LocalPath);
        var md5Text = await http.GetStringAsync(new Uri(url + ".md5"), ct);
        var md5 = md5Text.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? "";
        if (!Regex.IsMatch(md5, "^[0-9a-fA-F]{32}$"))
            throw new OsmDownloadException("Geofabrik's checksum file couldn't be read.");
        return new OsmRemote(url, name, DateFromName(name), r.Content.Headers.ContentLength, md5.ToLowerInvariant());
    }

    /// <summary>Data date from a Geofabrik file name such as australia-261103.osm.pbf (yyMMdd).</summary>
    public static DateOnly? DateFromName(string name)
    {
        var m = DatedName.Match(name);
        return m.Success && DateOnly.TryParseExact(m.Groups[1].Value, "yyMMdd", CultureInfo.InvariantCulture,
            DateTimeStyles.None, out var d) ? d : null;
    }

    /// <summary>True when the remote extract is the one already on this PC.</summary>
    public static bool IsSame(OsmExtract? cached, OsmRemote remote) =>
        cached is not null && (string.Equals(cached.Md5, remote.Md5, StringComparison.OrdinalIgnoreCase) ||
                               string.Equals(cached.FileName, remote.FileName, StringComparison.OrdinalIgnoreCase));

    /// <summary>Downloads (or resumes) the remote extract, checks its MD5 and makes it the cached one.</summary>
    public async Task<OsmExtract> DownloadAsync(OsmRemote remote, IProgress<DownloadProgress>? progress = null,
        CancellationToken ct = default)
    {
        Directory.CreateDirectory(folder);
        var final = Path.Combine(folder, remote.FileName);
        var part = final + ".part";
        foreach (var stale in Directory.EnumerateFiles(folder, "*.part").Where(p => !p.Equals(part, StringComparison.OrdinalIgnoreCase)))
            File.Delete(stale); // a half-finished older extract

        long have = File.Exists(part) ? new FileInfo(part).Length : 0;
        if (remote.Bytes is { } total)
            EnsureSpace(total - have + SpaceMargin);

        using (var req = new HttpRequestMessage(HttpMethod.Get, remote.Url))
        {
            if (have > 0)
                req.Headers.Range = new RangeHeaderValue(have, null);
            using var r = await http.SendAsync(req, HttpCompletionOption.ResponseHeadersRead, ct);
            if (r.StatusCode == HttpStatusCode.RequestedRangeNotSatisfiable && have > 0 && have == remote.Bytes)
            {
                // already complete
            }
            else
            {
                r.EnsureSuccessStatusCode();
                bool resume = have > 0 && r.StatusCode == HttpStatusCode.PartialContent;
                if (!resume)
                    have = 0;
                long? length = r.Content.Headers.ContentLength is { } cl ? cl + have : remote.Bytes;
                await using var src = await r.Content.ReadAsStreamAsync(ct);
                await using var dst = new FileStream(part, resume ? FileMode.Append : FileMode.Create, FileAccess.Write,
                    FileShare.None, 1 << 20, FileOptions.Asynchronous);
                await Pump(src, dst, have, length, DownloadPhase.Downloading, progress, ct);
            }
        }

        var md5 = await Md5Async(part, progress, ct);
        if (!md5.Equals(remote.Md5, StringComparison.OrdinalIgnoreCase))
        {
            File.Delete(part);
            throw new OsmDownloadException("The downloaded file was damaged (its checksum doesn't match). Please try again.");
        }
        File.Move(part, final, overwrite: true);
        return Commit(new OsmExtract(final, remote.FileName, remote.DataDate, new FileInfo(final).Length, md5,
            DateTime.UtcNow, remote.Url.ToString()));
    }

    /// <summary>Copies an extract the user already has (e.g. downloaded with a browser) into the cache.
    /// A matching .md5 file next to it is checked when present.</summary>
    public async Task<OsmExtract> ImportAsync(string path, IProgress<DownloadProgress>? progress = null,
        CancellationToken ct = default)
    {
        if (!LooksLikePbf(path))
            throw new OsmDownloadException("That file isn't an OpenStreetMap .osm.pbf extract.");
        Directory.CreateDirectory(folder);
        var name = Path.GetFileName(path);
        var final = Path.Combine(folder, name);
        var len = new FileInfo(path).Length;
        string? md5 = null;
        if (!Path.GetFullPath(path).Equals(Path.GetFullPath(final), StringComparison.OrdinalIgnoreCase))
        {
            EnsureSpace(len + SpaceMargin);
            var part = final + ".part";
            await using (var src = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20, FileOptions.Asynchronous))
            await using (var dst = new FileStream(part, FileMode.Create, FileAccess.Write, FileShare.None, 1 << 20, FileOptions.Asynchronous))
                await Pump(src, dst, 0, len, DownloadPhase.Copying, progress, ct);
            File.Move(part, final, overwrite: true);
            File.SetLastWriteTimeUtc(final, File.GetLastWriteTimeUtc(path));
        }
        var sidecar = path + ".md5";
        if (File.Exists(sidecar))
        {
            var expected = File.ReadAllText(sidecar).Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries).FirstOrDefault() ?? "";
            md5 = await Md5Async(final, progress, ct);
            if (!md5.Equals(expected, StringComparison.OrdinalIgnoreCase))
            {
                File.Delete(final);
                throw new OsmDownloadException("The file doesn't match the checksum in its .md5 file; it may be damaged.");
            }
        }
        return Commit(new OsmExtract(final, name, DateFromName(name), len, md5, DateTime.UtcNow, "file: " + name));
    }

    /// <summary>OSM PBF files start with a 4-byte length and a BlobHeader of type "OSMHeader".</summary>
    public static bool LooksLikePbf(string path)
    {
        try
        {
            using var f = File.OpenRead(path);
            var buf = new byte[32];
            int n = f.Read(buf, 0, buf.Length);
            return n >= 16 && System.Text.Encoding.ASCII.GetString(buf, 4, n - 4).Contains("OSMHeader");
        }
        catch (IOException) { return false; }
        catch (UnauthorizedAccessException) { return false; }
    }

    OsmExtract Commit(OsmExtract e)
    {
        // keep only this extract (other countries live in their own folders)
        foreach (var f in Directory.EnumerateFiles(folder, "*.osm.pbf"))
            if (!f.Equals(e.Path, StringComparison.OrdinalIgnoreCase))
                File.Delete(f);
        var tmp = Path.Combine(folder, InfoFile + ".tmp");
        File.WriteAllText(tmp, JsonSerializer.Serialize(e, Json));
        File.Move(tmp, Path.Combine(folder, InfoFile), overwrite: true);
        return e;
    }

    void EnsureSpace(long bytes)
    {
        var root = Path.GetPathRoot(Path.GetFullPath(folder));
        if (root is null || root.StartsWith(@"\\"))
            return;
        var free = new DriveInfo(root).AvailableFreeSpace;
        if (free < bytes)
            throw new OsmDownloadException(
                $"Not enough disk space: {bytes / 1e9:N1} GB needed on {root.TrimEnd('\\')}, {free / 1e9:N1} GB free.");
    }

    static async Task Pump(Stream src, Stream dst, long start, long? total, DownloadPhase phase,
        IProgress<DownloadProgress>? progress, CancellationToken ct)
    {
        var buf = new byte[1 << 20];
        long done = start, sinceTick = 0;
        var clock = System.Diagnostics.Stopwatch.StartNew();
        var tick = TimeSpan.Zero;
        double rate = 0;
        int n;
        while ((n = await src.ReadAsync(buf, ct)) > 0)
        {
            await dst.WriteAsync(buf.AsMemory(0, n), ct);
            done += n;
            sinceTick += n;
            var now = clock.Elapsed;
            if (now - tick >= TimeSpan.FromMilliseconds(250))
            {
                var inst = sinceTick / (now - tick).TotalSeconds;
                rate = rate == 0 ? inst : rate * 0.8 + inst * 0.2;
                tick = now;
                sinceTick = 0;
                progress?.Report(new DownloadProgress(phase, done, total, rate));
            }
        }
        progress?.Report(new DownloadProgress(phase, done, total, rate));
    }

    static async Task<string> Md5Async(string path, IProgress<DownloadProgress>? progress, CancellationToken ct)
    {
        await using var f = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, 1 << 20, FileOptions.Asynchronous);
        using var md5 = IncrementalHash.CreateHash(HashAlgorithmName.MD5);
        var buf = new byte[1 << 20];
        long done = 0;
        int n, k = 0;
        while ((n = await f.ReadAsync(buf, ct)) > 0)
        {
            md5.AppendData(buf, 0, n);
            done += n;
            if (++k % 16 == 0)
                progress?.Report(new DownloadProgress(DownloadPhase.Verifying, done, f.Length, 0));
        }
        return Convert.ToHexStringLower(md5.GetHashAndReset());
    }
}
