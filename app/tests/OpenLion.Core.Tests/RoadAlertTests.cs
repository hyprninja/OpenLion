using OpenLion.Core.Engine;
using OpenLion.Core.Osm;
using OpenLion.Core.Speed;

namespace OpenLion.Core.Tests;

public class RoadAlertTests
{
    [Fact]
    public void Preview_Australia_NamesOfficialLists()
    {
        var t = RoadAlerts.Preview([MapCountry.Australia]);
        Assert.Contains("School zones for New South Wales and level crossings for New South Wales and Western Australia", t);
        Assert.DoesNotContain("LINZ", t);
    }

    [Fact]
    public void Preview_NewZealand_MentionsLinz()
    {
        var t = RoadAlerts.Preview([MapCountry.NewZealand]);
        Assert.Contains("LINZ", t);
        Assert.DoesNotContain("School zones for", t);
    }

    [Fact]
    public void Describe_CountsFailuresAndLinz()
    {
        var r = new GovDataReport(
            [
                new("NSW_school_zones", "New South Wales school zones", "TfNSW", "ok", new DateOnly(2026, 11, 8), 3302),
                new("WA_crossings", "Western Australia rail crossings", "MRWA", "failed", new DateOnly(2026, 11, 1), 1170),
                new("NSW_crossings", "New South Wales level crossings", "TfNSW", "failed", null, null),
                new("NZ_addresses", "New Zealand addresses", "LINZ", "cached", new DateOnly(2026, 11, 2), 2_100_000),
                new("NZ_suburbs", "New Zealand suburbs and localities", "LINZ", "ok", new DateOnly(2026, 11, 8), 5100),
            ],
            [new("NSW", true, 3000, 9000, true, 2000), new("NZ", false, 10, 30, false, 500)]);
        var t = RoadAlerts.Describe(r);
        Assert.Contains("Western Australia rail crossings list couldn't be downloaded this time, so the copy from 1 November 2026", t);
        Assert.Contains("New South Wales level crossings list couldn't be downloaded, so OpenStreetMap's data", t);
        Assert.Contains("New Zealand: 2,100,000 addresses and 5,100 suburbs and localities from LINZ (downloaded 8 November 2026)", t);
        Assert.Contains("3,010 school zones (9,030 warnings where roads enter them) and 2,500 level crossing warnings", t);
    }

    [Fact]
    public void Parser_ReadsGovDataAndAlertEvents()
    {
        var seen = new List<GovDataReport>();
        var p = new MapBuild.Parser(null, null, new SyncProgress<GovDataReport>(seen.Add));
        p.Feed("""{"event": "gov_data", "final": false, "sources": [{"id": "NSW_school_zones", "name": "n", "provider": "p", "download": "ok", "fetched": "2026-11-08", "count": 3302}]}""");
        p.Feed("""{"event": "alerts", "states": [{"code": "NSW", "name": "New South Wales", "school_zones": {"source": "gov", "zones": 3000, "points": 9000}, "crossings": {"source": "osm+gov", "crossings": 2000}}]}""");
        p.Feed("""{"event": "result", "ok": true, "card": "c", "files": 1, "bytes": 2, "replaced": 3}""");
        var res = p.Finish(0);
        Assert.Equal(2, seen.Count);
        Assert.NotNull(res.GovData);
        Assert.Equal(3302, res.GovData!.Sources[0].Count);
        var nsw = res.GovData.States[0];
        Assert.True(nsw.OfficialZones);
        Assert.True(nsw.OfficialCrossings);
        Assert.Equal(9000, nsw.ZoneEntries);
    }

    [Fact]
    public void Arguments_PassGovDataFolder()
    {
        var req = new BuildRequest([("AU", "au.pbf")], "nav", null, "work", "out", GovDataFolder: @"C:\x\govdata");
        var args = MapBuild.Arguments(req);
        int i = args.IndexOf("--gov-data");
        Assert.True(i > 0);
        Assert.Equal(@"C:\x\govdata", args[i + 1]);
    }
}
