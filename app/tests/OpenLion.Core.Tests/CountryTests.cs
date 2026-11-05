using System.Net;
using OpenLion.Core.Engine;
using OpenLion.Core.Osm;

namespace OpenLion.Core.Tests;

public class CountryTests
{
    [Fact]
    public void FromCodes_KeepsCardOrderAndFallsBackToAustralia()
    {
        Assert.Equal([MapCountry.Australia, MapCountry.NewZealand], MapCountry.FromCodes(["nz", "AU"]));
        Assert.Equal([MapCountry.NewZealand], MapCountry.FromCodes(["NZ", "XX"]));
        Assert.Equal([MapCountry.Australia], MapCountry.FromCodes(null));
        Assert.Equal([MapCountry.Australia], MapCountry.FromCodes(["XX"]));
    }

    [Fact]
    public void Describe_JoinsTitles()
    {
        Assert.Equal("New Zealand", MapCountry.Describe([MapCountry.NewZealand]));
        Assert.Equal("Australia and New Zealand", MapCountry.Describe(MapCountry.All));
    }

    [Fact]
    public void OsmFolders_AustraliaKeepsItsOneZeroFolder()
    {
        Assert.Equal(AppPaths.OsmFolder, AppPaths.OsmFolderFor(MapCountry.Australia));
        Assert.Equal(Path.Combine(AppPaths.OsmFolder, "nz"), AppPaths.OsmFolderFor(MapCountry.NewZealand));
    }

    [Fact]
    public void Arguments_FirstExtractThenOneFlagPerCountry()
    {
        var req = new BuildRequest([("AU", @"C:\osm\australia.osm.pbf"), ("NZ", @"C:\osm\nz\new-zealand.osm.pbf")],
            @"C:\card\navigation", null, @"C:\work", @"C:\out\navigation");
        var a = MapBuild.Arguments(req);
        Assert.Equal(["-m", "openlion.make_card", @"AU=C:\osm\australia.osm.pbf", @"C:\card\navigation", @"C:\work",
            @"C:\out\navigation", "--json", "--extract", @"NZ=C:\osm\nz\new-zealand.osm.pbf"], a);
    }

    [Fact]
    public void Arguments_SingleExtractConstructorIsAustralia()
    {
        var a = MapBuild.Arguments(new BuildRequest(@"C:\a.osm.pbf", "nav", "m.tsv", "w", "o", [1, 2, 3, 4]));
        Assert.Equal(@"AU=C:\a.osm.pbf", a[2]);
        Assert.DoesNotContain("--extract", a);
        Assert.Equal(["--manifest", "m.tsv", "--bbox", "1", "2", "3", "4"], a[7..]);
    }

    [Fact]
    public void OsmKey_OneZeroCompatibleForAustralia()
    {
        var au = new OsmExtract("p", "australia-261103.osm.pbf", null, 1, null, DateTime.UtcNow, "x");
        var nz = au with { FileName = "new-zealand-261103.osm.pbf" };
        Assert.Equal("australia-261103.osm.pbf", BuildRecord.OsmKey([au]));
        Assert.Equal("australia-261103.osm.pbf+new-zealand-261103.osm.pbf", BuildRecord.OsmKey([au, nz]));
    }

    sealed class Recorder : HttpMessageHandler
    {
        public List<string> Urls { get; } = [];
        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage req, CancellationToken ct)
        {
            Urls.Add(req.RequestUri!.ToString());
            return Task.FromResult(new HttpResponseMessage(HttpStatusCode.NotFound));
        }
    }

    [Fact]
    public async Task Downloader_AsksForItsCountry()
    {
        using var t = new TempDir();
        var rec = new Recorder();
        var d = new OsmDownloader(new HttpClient(rec), t.Sub("nz"), MapCountry.NewZealand);
        await Assert.ThrowsAsync<HttpRequestException>(() => d.CheckLatestAsync());
        Assert.Equal(["https://download.geofabrik.de/australia-oceania/new-zealand-latest.osm.pbf"], rec.Urls);
    }
}
