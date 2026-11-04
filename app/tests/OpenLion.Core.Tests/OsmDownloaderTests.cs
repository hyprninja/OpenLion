using System.Net;
using System.Net.Http.Headers;
using System.Security.Cryptography;
using OpenLion.Core.Osm;

namespace OpenLion.Core.Tests;

/// <summary>Pretends to be Geofabrik: "-latest" resolves to a dated file, ranges are honoured
/// unless told not to.</summary>
sealed class FakeGeofabrik : HttpMessageHandler
{
    public const string Dated = "https://download.geofabrik.de/australia-oceania/australia-261103.osm.pbf";
    public byte[] Data { get; }
    public string Md5 { get; set; }
    public bool IgnoreRanges { get; set; }
    /// <summary>Corrupt one byte of what is served.</summary>
    public bool Corrupt { get; set; }
    public List<string?> Ranges { get; } = [];

    public FakeGeofabrik(int size = 3_000_000)
    {
        Data = new byte[size];
        new Random(1).NextBytes(Data);
        "\0\0\0\x0D\x0A\x09OSMHeader"u8.ToArray().CopyTo(Data, 0);
        Md5 = Convert.ToHexStringLower(MD5.HashData(Data));
    }

    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage req, CancellationToken ct)
    {
        var url = req.RequestUri!.ToString();
        if (url == OsmDownloader.LatestUrl.ToString())
        {
            var head = new HttpResponseMessage(HttpStatusCode.OK)
            {
                Content = new ByteArrayContent([]),
                RequestMessage = new HttpRequestMessage(req.Method, Dated), // as if redirected
            };
            head.Content.Headers.ContentLength = Data.Length;
            return Task.FromResult(head);
        }
        if (url == Dated + ".md5")
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.OK)
                { Content = new StringContent($"{Md5}  australia-261103.osm.pbf\n") });
        if (url == Dated)
        {
            var range = req.Headers.Range?.Ranges.FirstOrDefault();
            Ranges.Add(req.Headers.Range?.ToString());
            long from = !IgnoreRanges && range?.From is { } f ? f : 0;
            var body = Data.AsSpan((int)from).ToArray();
            if (Corrupt)
                body[body.Length / 2] ^= 0xFF;
            var r = new HttpResponseMessage(from > 0 ? HttpStatusCode.PartialContent : HttpStatusCode.OK)
                { Content = new ByteArrayContent(body) };
            if (from > 0)
                r.Content.Headers.ContentRange = new ContentRangeHeaderValue(from, Data.Length - 1, Data.Length);
            return Task.FromResult(r);
        }
        return Task.FromResult(new HttpResponseMessage(HttpStatusCode.NotFound));
    }
}

public class OsmDownloaderTests
{
    static (OsmDownloader, FakeGeofabrik) Make(TempDir t)
    {
        var fake = new FakeGeofabrik();
        return (new OsmDownloader(new HttpClient(fake), t.Sub("osm")), fake);
    }

    [Fact]
    public async Task CheckLatest_ResolvesDatedFile()
    {
        using var t = new TempDir();
        var (d, fake) = Make(t);
        var r = await d.CheckLatestAsync();
        Assert.Equal("australia-261103.osm.pbf", r.FileName);
        Assert.Equal(new DateOnly(2026, 11, 3), r.DataDate);
        Assert.Equal(fake.Md5, r.Md5);
        Assert.Equal(fake.Data.Length, r.Bytes);
    }

    [Fact]
    public async Task Download_VerifiesAndCaches()
    {
        using var t = new TempDir();
        var (d, fake) = Make(t);
        Assert.Null(d.Cached());
        var e = await d.DownloadAsync(await d.CheckLatestAsync());
        Assert.Equal(fake.Data, File.ReadAllBytes(e.Path));
        Assert.False(File.Exists(e.Path + ".part"));
        var c = d.Cached();
        Assert.NotNull(c);
        Assert.Equal(fake.Md5, c.Md5);
        Assert.Equal(new DateOnly(2026, 11, 3), c.DataDate);
        Assert.True(OsmDownloader.IsSame(c, await d.CheckLatestAsync()));
    }

    [Fact]
    public async Task Download_ResumesFromPartFile()
    {
        using var t = new TempDir();
        var (d, fake) = Make(t);
        Directory.CreateDirectory(d.Folder);
        File.WriteAllBytes(Path.Combine(d.Folder, "australia-261103.osm.pbf.part"), fake.Data[..1_000_000]);
        var e = await d.DownloadAsync(await d.CheckLatestAsync());
        Assert.Equal("bytes=1000000-", fake.Ranges.Single());
        Assert.Equal(fake.Data, File.ReadAllBytes(e.Path));
    }

    [Fact]
    public async Task Download_StartsOverWhenServerIgnoresRange()
    {
        using var t = new TempDir();
        var (d, fake) = Make(t);
        fake.IgnoreRanges = true;
        Directory.CreateDirectory(d.Folder);
        File.WriteAllBytes(Path.Combine(d.Folder, "australia-261103.osm.pbf.part"), new byte[500_000]); // junk
        var e = await d.DownloadAsync(await d.CheckLatestAsync());
        Assert.Equal(fake.Data, File.ReadAllBytes(e.Path));
    }

    [Fact]
    public async Task Download_RejectsDamagedFile()
    {
        using var t = new TempDir();
        var (d, fake) = Make(t);
        fake.Corrupt = true;
        await Assert.ThrowsAsync<OsmDownloadException>(async () => await d.DownloadAsync(await d.CheckLatestAsync()));
        Assert.Null(d.Cached());
        Assert.Empty(Directory.GetFiles(d.Folder, "*.pbf*"));
    }

    [Fact]
    public async Task Download_ReplacesOlderExtract()
    {
        using var t = new TempDir();
        var (d, fake) = Make(t);
        Directory.CreateDirectory(d.Folder);
        var old = Path.Combine(d.Folder, "australia-261001.osm.pbf");
        File.WriteAllBytes(old, fake.Data);
        await d.DownloadAsync(await d.CheckLatestAsync());
        Assert.False(File.Exists(old));
        Assert.Single(Directory.GetFiles(d.Folder, "*.osm.pbf"));
    }

    [Fact]
    public async Task Import_CopiesPbfAndChecksSidecarMd5()
    {
        using var t = new TempDir();
        var (d, fake) = Make(t);
        var src = t.Sub("mine", "australia-latest.osm.pbf");
        Directory.CreateDirectory(Path.GetDirectoryName(src)!);
        File.WriteAllBytes(src, fake.Data);
        File.WriteAllText(src + ".md5", fake.Md5 + "  australia-latest.osm.pbf\n");
        var e = await d.ImportAsync(src);
        Assert.Equal(fake.Md5, e.Md5);
        Assert.Null(e.DataDate);
        Assert.True(File.Exists(src)); // the user's file is left alone
        Assert.Equal(fake.Data, File.ReadAllBytes(e.Path));

        File.WriteAllText(src + ".md5", new string('0', 32));
        await Assert.ThrowsAsync<OsmDownloadException>(() => d.ImportAsync(src));
    }

    [Fact]
    public async Task Import_RejectsOtherFiles()
    {
        using var t = new TempDir();
        var (d, _) = Make(t);
        var src = t.File("notes.osm.pbf", "this is not a map");
        await Assert.ThrowsAsync<OsmDownloadException>(() => d.ImportAsync(src));
    }

    [Theory]
    [InlineData("australia-261103.osm.pbf", 2026, 11, 3)]
    [InlineData("AUSTRALIA-240229.OSM.PBF", 2024, 2, 29)]
    public void DateFromName_ReadsGeofabrikDates(string name, int y, int m, int day) =>
        Assert.Equal(new DateOnly(y, m, day), OsmDownloader.DateFromName(name));

    [Theory]
    [InlineData("australia-latest.osm.pbf")]
    [InlineData("australia-261399.osm.pbf")]
    public void DateFromName_NullWhenUnknown(string name) => Assert.Null(OsmDownloader.DateFromName(name));
}
