using OpenLion.Core.Engine;

namespace OpenLion.Core.Tests;

public class CompatParserTests
{
    const string Start = """{"event": "start", "db": "db_aunz", "tables": ["auz.rd", "auz.mp0"], "missing": [], "ambiguous": []}""";

    [Fact]
    public void ParsesACompatibleRun()
    {
        var seen = new List<CompatProgress>();
        var p = new CompatCheck.Parser(new SyncProgress<CompatProgress>(seen.Add));
        p.Feed(Start);
        p.Feed("""{"event": "progress", "table": "auz.mp0", "done": 500, "total": 1000}""");
        p.Feed("""{"event": "table", "table": "auz.rd", "kind": "rd", "identical": true, "first_diff": null}""");
        p.Feed("""{"event": "table", "table": "auz.mp0", "kind": "mp0", "identical": true}""");
        p.Feed("some text line");
        p.Feed("""{"event": "result", "ok": true, "db": "db_aunz", "failed": [], "missing": [], "ambiguous": []}""");
        var r = p.Finish(0, "db_aunz");

        Assert.True(r.Ok);
        Assert.Equal(2, r.Tables.Count);
        Assert.Equal(6, r.Log.Count);
        var prog = Assert.Single(seen);
        Assert.Equal(0.75, prog.Fraction, 3); // table 2 of 2, half done
    }

    [Fact]
    public void ParsesAFailedRun()
    {
        var p = new CompatCheck.Parser();
        p.Feed(Start);
        p.Feed("""{"event": "table", "table": "auz.mp0", "kind": "mp0", "identical": false, "error": "ValueError: boom"}""");
        p.Feed("""{"event": "result", "ok": false, "db": "db_aunz", "failed": ["auz.mp0"], "missing": ["cal"], "ambiguous": ["cat"]}""");
        var r = p.Finish(1, "db_aunz");

        Assert.False(r.Ok);
        Assert.Equal(["auz.mp0"], r.Failed);
        Assert.Equal(["cal", "cat"], r.Missing);
        Assert.Equal("ValueError: boom", r.Tables.Single().Error);
    }

    [Fact]
    public void EngineCrashWithoutResultIsAnError()
    {
        var p = new CompatCheck.Parser();
        p.Feed(Start);
        p.Feed(EngineRunner.StderrPrefix + "MemoryError");
        var r = p.Finish(1, "db_aunz");
        Assert.False(r.Ok);
        Assert.Contains("MemoryError", r.EngineError);
    }

    [Fact]
    public void ResultOkButNonZeroExitIsNotOk()
    {
        var p = new CompatCheck.Parser();
        p.Feed("""{"event": "result", "ok": true, "db": "db_aunz", "failed": [], "missing": []}""");
        Assert.False(p.Finish(3, "db_aunz").Ok);
    }
}

public class EngineRunnerTests
{
    static EnginePaths? Engine() => EngineLocator.Locate();

    [Fact]
    public async Task FindsDevEngineAndRunsPython()
    {
        var e = Engine();
        if (e is null)
            return; // no Python on this machine: nothing to test
        Assert.True(File.Exists(Path.Combine(e.EngineRoot, "openlion", "compat.py")));
        var lines = new List<string>();
        int code = await EngineRunner.RunAsync(e,
            ["-c", "import sys, openlion; print('hello'); print('oops', file=sys.stderr); sys.exit(3)"], lines.Add);
        Assert.Equal(3, code);
        Assert.Contains("hello", lines);
        Assert.Contains(EngineRunner.StderrPrefix + "oops", lines);
    }

    [Fact]
    public async Task CancelKillsTheEngine()
    {
        var e = Engine();
        if (e is null)
            return;
        using var cts = new CancellationTokenSource(TimeSpan.FromSeconds(1));
        var sw = System.Diagnostics.Stopwatch.StartNew();
        await Assert.ThrowsAnyAsync<OperationCanceledException>(() =>
            EngineRunner.RunAsync(e, ["-c", "import time; time.sleep(60)"], _ => { }, cts.Token));
        Assert.True(sw.Elapsed < TimeSpan.FromSeconds(20));
    }

    /// <summary>Full check against a real factory copy; set OPENLION_FACTORY_NAV to its navigation/ folder.
    /// Takes about 80 s, so it only runs when asked.</summary>
    [Fact]
    public async Task RealFactoryCardIsCompatible()
    {
        var nav = Environment.GetEnvironmentVariable("OPENLION_FACTORY_NAV");
        var e = Engine();
        if (nav is null || e is null)
            return;
        var r = await CompatCheck.RunAsync(e, nav);
        Assert.True(r.Ok, string.Join("\n", r.Log.TakeLast(20)));
        Assert.Equal(19, r.Tables.Count);
    }
}
