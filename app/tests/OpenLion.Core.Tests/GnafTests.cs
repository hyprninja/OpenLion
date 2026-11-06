using System.IO.Compression;
using System.Text.Json;
using OpenLion.Core.Addresses;
using OpenLion.Core.Engine;
using OpenLion.Core.Osm;

namespace OpenLion.Core.Tests;

public class GnafTests
{
    const string Base = "https://data.gov.au/data/dataset/19432f89-dc3a-4ef3-b943-5326ef1dbecc/resource";

    static JsonElement Reply(string resources) =>
        JsonDocument.Parse("""{"success": true, "result": {"resources": [""" + resources + "]}}").RootElement;

    static string Res(string name, string format, string url, string modified, long? size = null) =>
        $$"""{"name": "{{name}}", "format": "{{format}}", "url": "{{url}}", "last_modified": "{{modified}}"{{(size is { } s ? $", \"size\": {s}" : "")}}}""";

    [Fact]
    public void Newest_PicksTheLatestGda2020Zip()
    {
        var r = GnafDownloader.Newest(Reply(string.Join(",",
            Res("MAY 2026 - Geoscape G-NAF - GDA2020", "ZIP", $"{Base}/a/download/g-naf_may26_allstates_gda2020_psv_109.zip", "2026-05-17T03:00:00", 1_800_000_000),
            Res("AUG 2026 - Geoscape G-NAF - GDA2020", "ZIP", $"{Base}/b/download/g-naf_aug26_allstates_gda2020_psv_110.zip", "2026-08-17T03:24:56", 1_854_931_188),
            Res("AUG 2026 - Geoscape G-NAF - GDA94", "ZIP", $"{Base}/c/download/g-naf_aug26_allstates_gda94_psv_110.zip", "2026-08-18T00:00:00"),
            Res("GNAF Release Report August 2026", "PDF", $"{Base}/d/download/gnaf_release-report-aug-2026.pdf", "2026-08-19T00:00:00"),
            Res("Elsewhere", "ZIP", "https://example.com/g-naf_nov26_allstates_gda2020_psv_111.zip", "2026-11-01T00:00:00"))));
        Assert.Equal("g-naf_aug26_allstates_gda2020_psv_110.zip", r.FileName);
        Assert.Equal("AUG 2026 - Geoscape G-NAF - GDA2020", r.Name);
        Assert.Equal(1_854_931_188, r.Bytes);
    }

    [Fact]
    public void Newest_NoReleaseIsAnError()
    {
        Assert.Throws<GnafException>(() => GnafDownloader.Newest(Reply(
            Res("Report", "PDF", $"{Base}/d/download/report.pdf", "2026-08-19T00:00:00"))));
        Assert.Throws<GnafException>(() => GnafDownloader.Newest(JsonDocument.Parse("{}").RootElement));
    }

    [Theory]
    [InlineData("https://data.gov.au/x.zip", true)]
    [InlineData("http://data.gov.au/x.zip", false)]
    [InlineData("https://data.gov.au.evil.example/x.zip", false)]
    public void OnlyDataGovAu(string url, bool ok) => Assert.Equal(ok, GnafSource.Allowed(new Uri(url)));

    [Fact]
    public void Describe_FromFileName()
    {
        Assert.Equal("G-NAF August 2026", GnafDownloader.Describe("g-naf_aug26_allstates_gda2020_psv_110.zip"));
        Assert.Equal("my-addresses", GnafDownloader.Describe("my-addresses.zip"));
    }

    static string FakeGnaf(TempDir t, string name)
    {
        var p = t.Sub(name);
        using var z = ZipFile.Open(p, ZipArchiveMode.Create);
        using (var w = new StreamWriter(z.CreateEntry("G-NAF/G-NAF AUGUST 2026/Standard/ACT_ADDRESS_DETAIL_psv.psv").Open()))
            w.Write("ADDRESS_DETAIL_PID|NUMBER_FIRST\n");
        return p;
    }

    [Fact]
    public async Task Import_ChecksAndKeepsOneRelease()
    {
        using var t = new TempDir();
        var d = new GnafDownloader(new HttpClient(), t.Sub("gnaf"));
        Assert.Null(d.Cached());

        var notGnaf = t.Sub("other.zip");
        using (var z = ZipFile.Open(notGnaf, ZipArchiveMode.Create))
            z.CreateEntry("readme.txt");
        await Assert.ThrowsAsync<GnafException>(() => d.ImportAsync(notGnaf));
        await Assert.ThrowsAsync<GnafException>(() => d.ImportAsync(t.File("broken.zip", "not a zip")));

        var a = await d.ImportAsync(FakeGnaf(t, "g-naf_may26_allstates_gda2020_psv_109.zip"));
        Assert.Equal("G-NAF May 2026", a.Name);
        Assert.Equal(a.FileName, d.Cached()?.FileName);

        var b = await d.ImportAsync(FakeGnaf(t, "g-naf_aug26_allstates_gda2020_psv_110.zip"));
        Assert.Equal("g-naf_aug26_allstates_gda2020_psv_110.zip", d.Cached()?.FileName);
        Assert.False(File.Exists(a.Path));                 // the older release is removed
        Assert.True(GnafDownloader.IsSame(d.Cached(), new GnafRemote(new Uri($"{Base}/b/download/{b.FileName}"), b.FileName, "", null, null)));

        File.AppendAllText(b.Path, "x");                     // changed on disk: not trusted any more
        Assert.Null(d.Cached());
    }

    [Fact]
    public void BuildPassesTheAddressFile()
    {
        var req = new BuildRequest([("AU", "au.pbf")], "nav", null, "work", "out", Addresses: @"C:\g\gnaf.zip");
        var args = MapBuild.Arguments(req);
        Assert.Equal(@"C:\g\gnaf.zip", args[args.IndexOf("--addresses") + 1]);
        Assert.DoesNotContain("--addresses", MapBuild.Arguments(new BuildRequest([("AU", "au.pbf")], "nav", null, "work", "out")));
    }

    [Fact]
    public void BuildKey_ChangesWithTheRelease()
    {
        var osm = new[] { new OsmExtract("C:/osm/australia-261104.osm.pbf", "australia-261104.osm.pbf", null, 1, null, DateTime.UtcNow, "test") };
        var g = new GnafData("C:/g/x.zip", "g-naf_aug26_allstates_gda2020_psv_110.zip", "G-NAF August 2026", 1, null, DateTime.UtcNow);
        Assert.Equal(BuildRecord.OsmKey(osm), BuildRecord.Key(osm, null));      // no G-NAF: same key as before
        Assert.Equal("australia-261104.osm.pbf|gnaf:g-naf_aug26_allstates_gda2020_psv_110.zip", BuildRecord.Key(osm, g));
    }
}
