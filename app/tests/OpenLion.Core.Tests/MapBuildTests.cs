using OpenLion.Core.Drives;
using OpenLion.Core.Engine;

namespace OpenLion.Core.Tests;

public class MapBuildParserTests
{
    const string Start = """{"event": "start", "stages": [{"id": "roads", "label": "Reading roads", "weight": 100}, {"id": "fea0", "label": "Drawing", "weight": 300}, {"id": "check", "label": "Checking the new card", "weight": 100}]}""";

    [Fact]
    public void Progress_IsWeightedByStage()
    {
        var seen = new List<BuildProgress>();
        var p = new MapBuild.Parser(new SyncProgress<BuildProgress>(seen.Add));
        p.Feed(Start);
        p.Feed("""{"event": "stage", "stage": "roads"}""");
        p.Feed("[    12s] roads: 1,816,856 ways");
        p.Feed("""{"event": "stage", "stage": "fea0"}""");
        Assert.Equal(2, seen.Count);
        Assert.Equal(0.2, seen[1].StartFraction, 6);
        Assert.Equal(0.6, seen[1].StageFraction, 6);
        Assert.Equal("Drawing", seen[1].Stage.Label);
        // halfway through the stage by time; capped at 95% of the stage
        Assert.Equal(0.5, seen[1].Fraction(TimeSpan.FromSeconds(150)), 6);
        Assert.Equal(0.2 + 0.6 * 0.95, seen[1].Fraction(TimeSpan.FromHours(2)), 6);
    }

    [Fact]
    public void Result_Ok()
    {
        var p = new MapBuild.Parser();
        p.Feed(Start);
        p.Feed("""{"event": "result", "ok": true, "card": "C:\\b\\navigation", "files": 2225, "bytes": 571000000, "replaced": 18}""");
        var r = p.Finish(0);
        Assert.True(r.Ok);
        Assert.Equal(@"C:\b\navigation", r.CardPath);
        Assert.Equal(2225, r.Files);
        Assert.Equal(571_000_000, r.Bytes);
        Assert.Equal(18, r.Replaced);
        Assert.Null(r.Error);
    }

    [Fact]
    public void Result_Failure_NamesTheStage()
    {
        var p = new MapBuild.Parser();
        p.Feed(Start);
        p.Feed("""{"event": "stage", "stage": "check"}""");
        p.Feed("""{"event": "result", "ok": false, "stage": "check", "error": "the finished card failed its check (3 problems, 20s)", "error_type": "Failed", "traceback": null}""");
        var r = p.Finish(1);
        Assert.False(r.Ok);
        Assert.Equal("Checking the new card", r.FailedStage);
        Assert.Contains("3 problems", r.Error);
        Assert.Equal(3, r.Log.Count);
    }

    [Fact]
    public void NoResult_ReportsCrash()
    {
        var p = new MapBuild.Parser();
        p.Feed(Start);
        p.Feed("""{"event": "stage", "stage": "fea0"}""");
        p.Feed(EngineRunner.StderrPrefix + "MemoryError");
        var r = p.Finish(unchecked((int)0xC0000017));
        Assert.False(r.Ok);
        Assert.Equal("Drawing", r.FailedStage);
        Assert.Contains("run out of memory", r.Error);
        Assert.Contains("MemoryError", r.Error);
    }

    [Fact]
    public void OkResultWithNonZeroExit_IsFailure()
    {
        var p = new MapBuild.Parser();
        p.Feed("""{"event": "result", "ok": true, "card": "x", "files": 1, "bytes": 1, "replaced": 1}""");
        Assert.False(p.Finish(3).Ok);
    }
}

public class BuildRecordTests
{
    static FactoryCardRecord Factory(uint serial) => new() { Label = "", VolumeSerial = serial, CopyVerified = true };

    [Fact]
    public void Matches_SameCardAndExtractOnly()
    {
        using var t = new TempDir();
        var nav = Directory.CreateDirectory(t.Sub("navigation")).FullName;
        var b = new BuildRecord(nav, 0x1CF5423A, "australia-261103.osm.pbf", new DateOnly(2026, 11, 3), 2225, 1, DateTime.UtcNow, false);
        Assert.True(b.Matches(Factory(0x1CF5423A), "australia-261103.osm.pbf"));
        Assert.False(b.Matches(Factory(0x12345678), "australia-261103.osm.pbf"));
        Assert.False(b.Matches(Factory(0x1CF5423A), "australia-261110.osm.pbf"));
        Assert.False(b.Matches(null, "australia-261103.osm.pbf"));
        Directory.Delete(nav);
        Assert.False(b.Matches(Factory(0x1CF5423A), "australia-261103.osm.pbf"));
    }
}
