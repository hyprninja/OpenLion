using System.Text.Json;

namespace OpenLion.Core.Engine;

public sealed record CompatProgress(string Table, int TableIndex, int TableCount, int Done, int Total)
{
    /// <summary>Overall fraction 0..1.</summary>
    public double Fraction => TableCount == 0 ? 0 :
        (TableIndex + (Total == 0 ? 0 : Math.Min(1.0, (double)Done / Total))) / TableCount;
}

public sealed record CompatTable(string Table, bool Identical, string? Error, string Json);

public sealed record CompatResult
{
    public bool Ok { get; init; }
    public string Database { get; init; } = "";
    public IReadOnlyList<CompatTable> Tables { get; init; } = [];
    public IReadOnlyList<string> Failed { get; init; } = [];
    public IReadOnlyList<string> Missing { get; init; } = [];
    /// <summary>Set when the engine itself failed (crash, no result).</summary>
    public string? EngineError { get; init; }
    public int ExitCode { get; init; }
    public IReadOnlyList<string> Log { get; init; } = [];
}

/// <summary>Runs <c>python -m openlion.compat --json</c>: re-encodes every replaceable map table of
/// the copied card and requires byte-identical output. Proves OpenLion understands this card.</summary>
public static class CompatCheck
{
    public static async Task<CompatResult> RunAsync(EnginePaths engine, string navigationPath,
        IProgress<CompatProgress>? progress = null, CancellationToken ct = default, string database = "db_aunz")
    {
        var parser = new Parser(progress);
        int code = await EngineRunner.RunAsync(engine,
            ["-m", "openlion.compat", navigationPath, "--db", database, "--json"], parser.Feed, ct);
        return parser.Finish(code, database);
    }

    /// <summary>Turns the engine's output lines into a result. Public for tests.</summary>
    public sealed class Parser(IProgress<CompatProgress>? progress = null)
    {
        readonly object _lock = new();
        readonly List<string> _log = [];
        readonly List<CompatTable> _tables = [];
        List<string> _order = [];
        JsonElement? _result;

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
                switch (e.GetProperty("event").GetString())
                {
                    case "start":
                        _order = e.GetProperty("tables").EnumerateArray().Select(t => t.GetString()!).ToList();
                        break;
                    case "progress":
                        var t = e.GetProperty("table").GetString()!;
                        progress?.Report(new CompatProgress(t, Math.Max(0, _order.IndexOf(t)), _order.Count,
                            e.GetProperty("done").GetInt32(), e.GetProperty("total").GetInt32()));
                        break;
                    case "table":
                        _tables.Add(new CompatTable(e.GetProperty("table").GetString()!,
                            e.GetProperty("identical").GetBoolean(),
                            e.TryGetProperty("error", out var err) ? err.GetString() : null, line));
                        break;
                    case "result":
                        _result = e;
                        break;
                }
            }
        }

        public CompatResult Finish(int exitCode, string database)
        {
            lock (_lock)
            {
                if (_result is not { } r)
                {
                    var tail = _log.Where(l => l.StartsWith(EngineRunner.StderrPrefix)).TakeLast(3)
                        .Select(l => l[EngineRunner.StderrPrefix.Length..]);
                    return new CompatResult
                    {
                        Database = database, Tables = [.. _tables], ExitCode = exitCode, Log = [.. _log],
                        EngineError = $"The map engine stopped without a result (exit code {exitCode}). " + string.Join(" ", tail),
                    };
                }
                static List<string> Strings(JsonElement e, string name) =>
                    e.TryGetProperty(name, out var a) ? a.EnumerateArray().Select(x => x.GetString()!).ToList() : [];
                return new CompatResult
                {
                    Ok = r.GetProperty("ok").GetBoolean() && exitCode == 0,
                    Database = database,
                    Tables = [.. _tables],
                    Failed = Strings(r, "failed"),
                    Missing = [.. Strings(r, "missing"), .. Strings(r, "ambiguous")],
                    EngineError = r.TryGetProperty("error", out var err) ? err.GetString() : null,
                    ExitCode = exitCode,
                    Log = [.. _log],
                };
            }
        }
    }
}
