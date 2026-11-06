using System.Net;
using System.Text.Json;
using OpenLion.Core.Engine;
using OpenLion.Core.Osm;
using OpenLion.Core.Speed;

namespace OpenLion.Core.Tests;

public class SpeedZoneTests
{
    const string Base = "https://opendata.transport.vic.gov.au/dataset/975b/resource/";

    static JsonElement Reply(params (string format, string url, string name, string? modified)[] res)
    {
        var items = res.Select(r => new Dictionary<string, object?>
        {
            ["format"] = r.format, ["url"] = r.url, ["name"] = r.name, ["last_modified"] = r.modified,
        });
        return JsonDocument.Parse(JsonSerializer.Serialize(new { result = new { resources = items } })).RootElement;
    }

    [Fact]
    public void Newest_PicksLatestGeoJson()
    {
        var r = SpeedZoneDownloader.Newest(Reply(
            ("GeoJSON", Base + "a/download/speed_zones_may_2026.geojson", "Speed Zones May 2026", "2026-06-16T03:54:33"),
            ("GeoJSON", Base + "b/download/speed_zones_september_2026.geojson", "Speed Zones September 2026", "2026-10-06T03:40:12"),
            ("CSV", Base + "c/download/speed_zones_october_2026.csv", "Speed Zones October 2026 CSV", "2026-11-01T00:00:00"),
            ("GeoJSON", Base + "d/download/speed_zones_june_2023.geojson", "Speed Zones June 2023", "2025-04-07T01:10:03")),
            SpeedZoneSource.Victoria);
        Assert.Equal("speed_zones_september_2026.geojson", r.FileName);
        Assert.Equal("Speed Zones September 2026", r.Name);
    }

    [Fact]
    public void Newest_NswGeoJsonLabelledData_GetsADatedName()
    {
        const string nsw = "https://opendata.transport.nsw.gov.au/data/dataset/4253/resource/";
        var r = SpeedZoneDownloader.Newest(Reply(
            ("ZIP", nsw + "a/download/speedzones.zip", "Speed Zones data", "2026-11-06T18:20:00"),
            ("data", nsw + "b/download/speed_zones.geojson", "Speed Zones - GeoJSON Format", "2026-11-06T18:20:17"),
            ("CSV", nsw + "c/download/speed_zones.csv", "Speed Zones - CSV Format", "2026-11-06T18:20:30")),
            SpeedZoneSource.NewSouthWales);
        Assert.Equal("speed_zones_2026-11-06.geojson", r.FileName);
        Assert.Equal("Speed Zones 6 November 2026", r.Name);
        Assert.EndsWith("/speed_zones.geojson", r.Url.AbsolutePath);
    }

    [Theory]
    [InlineData("https://evil.example.com/speed_zones.geojson")]
    [InlineData("http://opendata.transport.vic.gov.au/x/speed_zones.geojson")]
    public void Newest_IgnoresFilesFromOtherHostsOrPlainHttp(string url)
    {
        var e = Assert.Throws<SpeedZoneException>(() => SpeedZoneDownloader.Newest(
            Reply(("GeoJSON", url, "Speed Zones November 2026", "2026-12-01T00:00:00")), SpeedZoneSource.Victoria));
        Assert.Contains("no GeoJSON", e.Message);
    }

    [Fact]
    public void Newest_StrangeReply_Throws()
    {
        var bad = JsonDocument.Parse("""{"success": false}""").RootElement;
        Assert.Throws<SpeedZoneException>(() => SpeedZoneDownloader.Newest(bad, SpeedZoneSource.Victoria));
    }

    [Fact]
    public void VictoriaAndWaAreAvailable_SaAndNtNotListed()
    {
        Assert.Equal(["VIC", "WA", "NSW", "QLD", "TAS", "ACT", "NZ"], SpeedZoneSource.All.Select(s => s.Code));
        Assert.Same(SpeedZoneSource.NewSouthWales, SpeedZoneSource.ByCode("nsw"));
        Assert.Null(SpeedZoneSource.NewSouthWales.EngineFetch);
        Assert.Null(SpeedZoneSource.ByCode("SA"));
        Assert.Null(SpeedZoneSource.ByCode("NT"));
        Assert.Equal(["South Australia", "the Northern Territory"], SpeedZoneSource.NotOpen);
        Assert.Equal(["VIC", "WA", "NSW", "NZ"], SpeedZoneSource.All.Where(s => s.Available).Select(s => s.Code));
        Assert.All(SpeedZoneSource.All.Where(s => s.Code != "NZ"), s => Assert.Equal(MapCountry.Australia, s.Country));
        Assert.Equal(MapCountry.NewZealand, SpeedZoneSource.NewZealand.Country);
        Assert.Equal("fetch-nz", SpeedZoneSource.NewZealand.EngineFetch);
        Assert.All(SpeedZoneSource.All.Where(s => s.Available), s => Assert.Contains("CC BY", s.Credit));
        Assert.Same(SpeedZoneSource.Victoria, SpeedZoneSource.ByCode("vic"));
        Assert.Same(SpeedZoneSource.WesternAustralia, SpeedZoneSource.ByCode("WA"));
        Assert.Null(SpeedZoneSource.Victoria.EngineFetch);
        Assert.Equal("fetch-wa", SpeedZoneSource.WesternAustralia.EngineFetch);
    }

    [Fact]
    public void Dated_UsesTheCatalogueDateAsVersion()
    {
        var reply = JsonDocument.Parse("""{"result": {"title": "Legal Speed Limits", "metadata_modified": "2026-11-05T21:24:27.181655"}}""").RootElement;
        var r = SpeedZoneDownloader.Dated(reply, SpeedZoneSource.WesternAustralia);
        Assert.Equal("wa-2026-11-05", r.FileName);
        Assert.Equal("Legal Speed Limits 5 November 2026", r.Name);
        Assert.Equal("catalogue.data.wa.gov.au", r.Url.Host);
    }

    [Fact]
    public void Dated_ReadsAnArcGisLayersLastEdit()
    {
        // 1793950037481 ms = 2026-11-06 (UTC)
        var reply = JsonDocument.Parse("""{"name": "SpeedLimitZoneFull", "editingInfo": {"lastEditDate": 1793950037481, "dataLastEditDate": 1793950037481}}""").RootElement;
        var r = SpeedZoneDownloader.Dated(reply, SpeedZoneSource.NewZealand);
        Assert.Equal("nz-2026-11-06", r.FileName);
        Assert.Equal("National Speed Limit Register 6 November 2026", r.Name);
        Assert.Equal("services.arcgis.com", r.Url.Host);
    }

    [Fact]
    public void Dated_NoDate_Throws()
    {
        var reply = JsonDocument.Parse("""{"result": {"title": "Legal Speed Limits"}}""").RootElement;
        Assert.Throws<SpeedZoneException>(() => SpeedZoneDownloader.Dated(reply, SpeedZoneSource.WesternAustralia));
    }

    [Fact]
    public void Cached_NeedsMatchingInfoAndFile()
    {
        using var t = new TempDir();
        var dl = new SpeedZoneDownloader(new HttpClient(), t.Path, SpeedZoneSource.Victoria);
        Assert.Null(dl.Cached());
        var bin = t.File("zones.bin", "0123456789");
        var data = new SpeedZoneData("VIC", bin, "zones.bin", "Speed Zones September 2026",
            "speed_zones_september_2026.geojson", 10, null, DateTime.UtcNow);
        File.WriteAllText(t.Sub("zones.json"), JsonSerializer.Serialize(data));
        Assert.Equal("Speed Zones September 2026", dl.Cached()?.Name);
        Assert.True(SpeedZoneDownloader.IsSame(dl.Cached(),
            new SpeedZoneRemote(new Uri(Base + "speed_zones_september_2026.geojson"), "speed_zones_september_2026.geojson", "x", null)));
        File.WriteAllText(bin, "short");     // size no longer matches: not trusted
        Assert.Null(dl.Cached());
    }

    [Fact]
    public async Task Download_RefusesOtherHosts()
    {
        using var t = new TempDir();
        var dl = new SpeedZoneDownloader(new HttpClient(), t.Path, SpeedZoneSource.Victoria);
        var remote = new SpeedZoneRemote(new Uri("https://evil.example.com/z.geojson"), "z.geojson", "z", null);
        await Assert.ThrowsAsync<SpeedZoneException>(() =>
            dl.DownloadAsync(remote, new EnginePaths("python", t.Path, false)));
        Assert.Empty(Directory.EnumerateFiles(t.Path));
    }

    [Fact]
    public void BuildKey_AddsSpeedDataOnlyWhenUsed()
    {
        var osm = new[] { new OsmExtract("a", "australia-261103.osm.pbf", null, 1, null, DateTime.UtcNow, "x") };
        Assert.Equal("australia-261103.osm.pbf", BuildRecord.Key(osm, []));
        var vic = new SpeedZoneData("VIC", "p", "zones.bin", "Speed Zones September 2026",
            "speed_zones_september_2026.geojson", 1, null, DateTime.UtcNow);
        Assert.Equal("australia-261103.osm.pbf|speed:VIC=speed_zones_september_2026.geojson", BuildRecord.Key(osm, [vic]));
    }

    [Fact]
    public void Arguments_PassSpeedZones()
    {
        var req = new BuildRequest([("AU", "au.pbf")], "nav", null, "work", "out", null, ["speed/vic/zones.bin"]);
        var args = MapBuild.Arguments(req);
        int i = args.IndexOf("--speed-zones");
        Assert.True(i > 0);
        Assert.Equal("speed/vic/zones.bin", args[i + 1]);
        Assert.DoesNotContain("--speed-zones", MapBuild.Arguments(req with { SpeedZones = null }));
    }
}
