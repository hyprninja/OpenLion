using System.Globalization;
using System.Text.Json;
using OpenLion.Core.Osm;

namespace OpenLion.Core.Speed;

/// <summary>Where one state's (or New Zealand's) speed cameras on the card come from: an official
/// list the engine downloaded ("gov") or OpenStreetMap ("osm"). Reported by make_card's "cameras" event.</summary>
public sealed record CameraState(string Code, string Name, string Source, string Provider, DateOnly? Fetched,
    int? Cameras, string? Download)
{
    public bool Official => Source == "gov";
    public bool DownloadFailed => Download == "failed";

    public static CameraState FromJson(JsonElement e)
    {
        string? Str(string n) => e.TryGetProperty(n, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
        DateOnly? date = DateOnly.TryParseExact(Str("fetched"), "yyyy-MM-dd", CultureInfo.InvariantCulture,
            DateTimeStyles.None, out var d) ? d : null;
        int? count = e.TryGetProperty("cameras", out var c) && c.ValueKind == JsonValueKind.Number ? c.GetInt32() : null;
        return new CameraState(Str("code") ?? "", Str("name") ?? "", Str("source") ?? "osm", Str("provider") ?? "",
            date, count, Str("download"));
    }
}

/// <summary>Speed camera sources and the messages the build step shows about them.</summary>
public static class SpeedCameras
{
    /// <summary>States whose governments publish fixed camera locations openly (a plain download,
    /// with coordinates, kept current). Mirrors the engine's openlion/gov_cameras.py SOURCES.</summary>
    public static readonly IReadOnlyList<string> OfficialStates = ["VIC", "ACT"];

    static readonly string[] AuStates = ["NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"];

    /// <summary>How a state reads in a sentence.</summary>
    public static string Place(string code) => code switch
    {
        "NSW" => "New South Wales", "VIC" => "Victoria", "QLD" => "Queensland", "SA" => "South Australia",
        "WA" => "Western Australia", "TAS" => "Tasmania", "NT" => "the Northern Territory", "ACT" => "the ACT",
        "NZ" => "New Zealand", _ => code,
    };

    static string Possessive(string code) => code switch
    {
        "NT" => "The Northern Territory's", "ACT" => "The ACT's", _ => Place(code) + "'s",
    };

    /// <summary>"a, b and c".</summary>
    public static string JoinAnd(IReadOnlyList<string> items) => items.Count switch
    {
        0 => "",
        1 => items[0],
        _ => string.Join(", ", items.Take(items.Count - 1)) + " and " + items[^1],
    };

    /// <summary>The states and countries a build covers, in the engine's order.</summary>
    public static IReadOnlyList<string> Codes(IEnumerable<MapCountry> countries)
    {
        var list = countries.ToList();
        var codes = new List<string>();
        if (list.Any(c => c.Code == "AU"))
            codes.AddRange(AuStates);
        if (list.Any(c => c.Code == "NZ"))
            codes.Add("NZ");
        return codes;
    }

    /// <summary>Before a build: what to expect, from <see cref="OfficialStates"/>.</summary>
    public static string Preview(IEnumerable<MapCountry> countries)
    {
        var codes = Codes(countries);
        var official = codes.Where(c => OfficialStates.Contains(c)).ToList();
        var guess = codes.Where(c => !OfficialStates.Contains(c)).ToList();
        var parts = new List<string>();
        if (official.Count > 0)
            parts.Add($"Speed cameras for {JoinAnd(official.Select(Place).ToList())} are downloaded fresh from the " +
                      "official lists when the build starts.");
        if (guess.Count > 0)
            parts.Add(Snark(guess));
        return string.Join(" ", parts);
    }

    /// <summary>The line for states with no open camera list.</summary>
    public static string Snark(IReadOnlyList<string> codes)
    {
        bool one = codes.Count == 1;
        return $"You can't have up-to-date speed cameras for {JoinAnd(codes.Select(Place).ToList())} because " +
               $"{(one ? "it doesn't" : "they don't")} publish {(one ? "its" : "their")} camera locations in an open format. " +
               "You'll get OpenStreetMap's best guess instead: crowd-sourced, and only as current as the last " +
               "mapper who drove past.";
    }

    /// <summary>During and after a build: what the engine actually used.</summary>
    public static string Describe(IReadOnlyList<CameraState> states)
    {
        if (states.Count == 0)
            return "";
        var lines = new List<string>();
        var official = states.Where(s => s.Official).ToList();
        if (official.Count > 0)
        {
            var dates = official.Where(s => s.Fetched is not null).Select(s => s.Fetched!.Value).Distinct().ToList();
            var when = dates.Count == 1 ? $" (downloaded {dates[0].ToString("d MMMM yyyy", CultureInfo.InvariantCulture)})" : "";
            lines.Add($"Speed cameras up to date from the official lists for {JoinAnd(official.Select(s => Place(s.Code)).ToList())}{when}.");
        }
        foreach (var s in official.Where(s => s.DownloadFailed))
            lines.Add($"{Possessive(s.Code)} list couldn't be downloaded this time, so its cameras come from the copy " +
                      $"downloaded on {s.Fetched?.ToString("d MMMM yyyy", CultureInfo.InvariantCulture) ?? "an earlier day"}.");
        var lost = states.Where(s => !s.Official && OfficialStates.Contains(s.Code)).ToList();
        if (lost.Count > 0)
            lines.Add($"The official list for {JoinAnd(lost.Select(s => Place(s.Code)).ToList())} couldn't be downloaded, " +
                      "so it gets OpenStreetMap's cameras this time. Build again later to try again.");
        var guess = states.Where(s => !s.Official && !OfficialStates.Contains(s.Code)).Select(s => s.Code).ToList();
        if (guess.Count > 0)
            lines.Add(Snark(guess));
        int total = states.Sum(s => s.Cameras ?? 0);
        if (states.Any(s => s.Cameras is not null))
            lines.Add($"{total:N0} camera warnings on the card (one per direction a camera checks).");
        return string.Join(Environment.NewLine, lines);
    }
}
