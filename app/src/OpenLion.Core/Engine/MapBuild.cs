using System.Diagnostics;
using System.Globalization;
using System.Text.Json;

namespace OpenLion.Core.Engine;

public sealed record BuildStage(string Id, string Label, double Weight);

/// <summary>Build progress: the current stage and how far through the whole build it starts.</summary>
public sealed record BuildProgress(BuildStage Stage, int Index, int Count, double StartFraction, double StageFraction,
    double TotalWeight)
{
    /// <summary>Overall fraction 0..1, moving through the stage by elapsed time against its usual
    /// length (never past 95% of it, since stages often run longer on slower PCs).</summary>
    public double Fraction(TimeSpan inStage) =>
        StartFraction + StageFraction * Math.Min(0.95, Stage.Weight <= 0 ? 0 : inStage.TotalSeconds / Stage.Weight);
}

public sealed record BuildResult
{
    public bool Ok { get; init; }
    public string? CardPath { get; init; }
    public int Files { get; init; }
    public long Bytes { get; init; }
    public int Replaced { get; init; }
    /// <summary>Stage that failed, if known.</summary>
    public string? FailedStage { get; init; }
    public string? Error { get; init; }
    public int ExitCode { get; init; }
    public IReadOnlyList<string> Log { get; init; } = [];
}

/// <param name="Pbf">OSM extract.</param>
/// <param name="FactoryNavigation">navigation/ folder of the factory card copy.</param>
/// <param name="Manifest">manifest.tsv written when the factory card was copied.</param>
/// <param name="WorkFolder">Engine working folder (cached OSM reads, new tables).</param>
/// <param name="OutputNavigation">navigation/ folder to create.</param>
/// <param name="Bbox">Optional area (lon0 lat0 lon1 lat1) for quick test builds.</param>
public sealed record BuildRequest(string Pbf, string FactoryNavigation, string? Manifest, string WorkFolder,
    string OutputNavigation, double[]? Bbox = null);

/// <summary>Runs <c>python -m openlion.make_card --json</c>: OSM extract + factory copy to a
/// finished, checked navigation/ folder.</summary>
public static class MapBuild
{
    /// <summary>Reads a test area from OPENLION_BBOX ("lon0 lat0 lon1 lat1"), for development.</summary>
    public static double[]? BboxFromEnvironment()
    {
        var s = Environment.GetEnvironmentVariable("OPENLION_BBOX");
        if (string.IsNullOrWhiteSpace(s))
            return null;
        var v = s.Split([' ', ','], StringSplitOptions.RemoveEmptyEntries)
            .Select(x => double.Parse(x, CultureInfo.InvariantCulture)).ToArray();
        return v.Length == 4 ? v : null;
    }

    public static async Task<BuildResult> RunAsync(EnginePaths engine, BuildRequest req,
        IProgress<BuildProgress>? progress = null, CancellationToken ct = default)
    {
        var args = new List<string>
        {
            "-m", "openlion.make_card", req.Pbf, req.FactoryNavigation, req.WorkFolder, req.OutputNavigation, "--json",
        };
        if (req.Manifest is not null)
            args.AddRange(["--manifest", req.Manifest]);
        if (req.Bbox is { Length: 4 } b)
            args.AddRange(["--bbox", .. b.Select(x => x.ToString(CultureInfo.InvariantCulture))]);
        var parser = new Parser(progress);
        // Below normal priority: the build takes most of an hour and the PC should stay usable.
        int code = await EngineRunner.RunAsync(engine, args, parser.Feed, ct, ProcessPriorityClass.BelowNormal);
        return parser.Finish(code);
    }

    /// <summary>Turns make_card's output lines into progress and a result. Public for tests.</summary>
    public sealed class Parser(IProgress<BuildProgress>? progress = null)
    {
        readonly object _lock = new();
        readonly List<string> _log = [];
        List<BuildStage> _stages = [];
        string? _current;
        JsonElement? _result;

        public IReadOnlyList<BuildStage> Stages { get { lock (_lock) return [.. _stages]; } }

        public void Feed(string line)
        {
            lock (_lock)
            {
                _log.Add(line);
                if (!line.StartsWith('{'))
                    return;
                JsonElement e;
                try { e = JsonDocument.Parse(line).RootElement.Clone(); }
                catch (JsonException) { return; }
                if (!e.TryGetProperty("event", out var ev))
                    return;
                switch (ev.GetString())
                {
                    case "start":
                        _stages = e.GetProperty("stages").EnumerateArray()
                            .Select(s => new BuildStage(s.GetProperty("id").GetString()!, s.GetProperty("label").GetString()!,
                                s.GetProperty("weight").GetDouble()))
                            .ToList();
                        break;
                    case "stage":
                        _current = e.GetProperty("stage").GetString();
                        int i = _stages.FindIndex(s => s.Id == _current);
                        if (i >= 0 && progress is not null)
                        {
                            double total = _stages.Sum(s => s.Weight);
                            double before = _stages.Take(i).Sum(s => s.Weight);
                            progress.Report(new BuildProgress(_stages[i], i, _stages.Count,
                                total == 0 ? 0 : before / total, total == 0 ? 0 : _stages[i].Weight / total, total));
                        }
                        break;
                    case "result":
                        _result = e;
                        break;
                }
            }
        }

        string? Label(string? id) => _stages.FirstOrDefault(s => s.Id == id)?.Label ?? id;

        public BuildResult Finish(int exitCode)
        {
            lock (_lock)
            {
                if (_result is not { } r)
                {
                    var tail = _log.Where(l => l.StartsWith(EngineRunner.StderrPrefix)).TakeLast(3)
                        .Select(l => l[EngineRunner.StderrPrefix.Length..]);
                    // 0xC0000017 no memory, 0xC0000005 access violation, 0xC00000FD stack overflow
                    var hint = exitCode is unchecked((int)0xC0000017) or unchecked((int)0xC0000005) or unchecked((int)0xC00000FD)
                        ? " The PC may have run out of memory."
                        : "";
                    return new BuildResult
                    {
                        ExitCode = exitCode, Log = [.. _log], FailedStage = Label(_current),
                        Error = $"The map engine stopped without finishing (exit code {exitCode}).{hint} " + string.Join(" ", tail),
                    };
                }
                bool ok = r.GetProperty("ok").GetBoolean() && exitCode == 0;
                string? Str(string name) => r.TryGetProperty(name, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
                return new BuildResult
                {
                    Ok = ok,
                    CardPath = Str("card"),
                    Files = r.TryGetProperty("files", out var f) ? f.GetInt32() : 0,
                    Bytes = r.TryGetProperty("bytes", out var b) ? b.GetInt64() : 0,
                    Replaced = r.TryGetProperty("replaced", out var rp) ? rp.GetInt32() : 0,
                    FailedStage = ok ? null : Label(Str("stage") ?? _current),
                    Error = ok ? null : Str("error") ?? $"exit code {exitCode}",
                    ExitCode = exitCode,
                    Log = [.. _log],
                };
            }
        }
    }
}
