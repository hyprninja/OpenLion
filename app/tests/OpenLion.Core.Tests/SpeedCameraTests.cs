using OpenLion.Core.Engine;
using OpenLion.Core.Osm;
using OpenLion.Core.Speed;

namespace OpenLion.Core.Tests;

public class SpeedCameraTests
{
    static CameraState S(string code, string source, int? n = null, string? download = null, string? fetched = "2026-11-06") =>
        new(code, code, source, "", fetched is null ? null : DateOnly.Parse(fetched), n, download);

    [Fact]
    public void Preview_Australia_NamesOfficialAndGuessedStates()
    {
        var t = SpeedCameras.Preview([MapCountry.Australia]);
        Assert.Contains("Speed cameras for Victoria and the ACT are downloaded fresh", t);
        Assert.Contains("You can't have up-to-date speed cameras for New South Wales, Queensland, South Australia, " +
                        "Western Australia, Tasmania and the Northern Territory because they don't publish", t);
        Assert.Contains("OpenStreetMap's best guess", t);
    }

    [Fact]
    public void Preview_NewZealandOnly_SingularSnark()
    {
        var t = SpeedCameras.Preview([MapCountry.NewZealand]);
        Assert.DoesNotContain("downloaded fresh", t);
        Assert.Contains("for New Zealand because it doesn't publish its camera locations", t);
    }

    [Fact]
    public void Describe_UpToDateStatesFailedDownloadAndCount()
    {
        var t = SpeedCameras.Describe([
            S("NSW", "osm", 300, fetched: null), S("VIC", "gov", 400, "ok"), S("ACT", "gov", 30, "failed", "2026-11-01"),
        ]);
        Assert.Contains("up to date from the official lists for Victoria and the ACT.", t);   // two dates: none shown
        Assert.Contains("The ACT's list couldn't be downloaded this time", t);
        Assert.Contains("1 November 2026", t);
        Assert.Contains("for New South Wales because it doesn't", t);
        Assert.Contains("730 camera warnings", t);
    }

    [Fact]
    public void Describe_OfficialStateThatNeverDownloaded_IsNotBlamed()
    {
        var t = SpeedCameras.Describe([S("VIC", "osm", 10, "failed", null), S("QLD", "osm", 5, fetched: null)]);
        Assert.Contains("The official list for Victoria couldn't be downloaded", t);
        Assert.Contains("for Queensland because", t);
        Assert.DoesNotContain("for Victoria and Queensland because", t);
    }

    [Fact]
    public void Parser_ReadsCameraEvents()
    {
        var seen = new List<IReadOnlyList<CameraState>>();
        var p = new MapBuild.Parser(null, new SyncProgress<IReadOnlyList<CameraState>>(seen.Add));
        p.Feed("""{"event": "start", "stages": [{"id": "roads", "label": "Reading roads", "weight": 1}]}""");
        p.Feed("""{"event": "cameras", "final": false, "states": [{"code": "VIC", "name": "Victoria", "source": "gov", "provider": "x", "fetched": "2026-11-06", "cameras": null, "download": "ok"}]}""");
        p.Feed("""{"event": "cameras", "final": true, "states": [{"code": "VIC", "name": "Victoria", "source": "gov", "provider": "x", "fetched": "2026-11-06", "cameras": 412, "download": "ok"}]}""");
        p.Feed("""{"event": "result", "ok": true, "card": "c", "files": 1, "bytes": 2, "replaced": 3}""");
        var r = p.Finish(0);
        Assert.Equal(2, seen.Count);
        Assert.Null(seen[0][0].Cameras);
        Assert.Equal(412, r.Cameras.Single().Cameras);
        Assert.Equal(new DateOnly(2026, 11, 6), r.Cameras[0].Fetched);
    }

    [Fact]
    public void Arguments_PassCameraFolder()
    {
        var req = new BuildRequest([("AU", "a.pbf")], "nav", null, "work", "out", CameraFolder: @"C:\x\cameras");
        var args = MapBuild.Arguments(req);
        int i = args.IndexOf("--cameras");
        Assert.True(i > 0);
        Assert.Equal(@"C:\x\cameras", args[i + 1]);
    }

    sealed class SyncProgress<T>(Action<T> f) : IProgress<T>
    {
        public void Report(T value) => f(value);
    }
}
