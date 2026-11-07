using System.Globalization;
using System.Text.Json;
using OpenLion.Core.Osm;

namespace OpenLion.Core.Speed;

/// <summary>One official list the engine keeps in its gov-data folder (make_card's "gov_data" event):
/// a school zone or level crossing list, or New Zealand's LINZ addresses and suburbs.</summary>
public sealed record GovDataSource(string Id, string Name, string Provider, string? Download, DateOnly? Fetched, int? Count)
{
    public bool Usable => Fetched is not null;
    public bool DownloadFailed => Download == "failed";
    public bool IsNewZealand => Id.StartsWith("NZ_", StringComparison.Ordinal);

    public static GovDataSource FromJson(JsonElement e)
    {
        string? Str(string n) => e.TryGetProperty(n, out var v) && v.ValueKind == JsonValueKind.String ? v.GetString() : null;
        DateOnly? date = DateOnly.TryParseExact(Str("fetched"), "yyyy-MM-dd", CultureInfo.InvariantCulture,
            DateTimeStyles.None, out var d) ? d : null;
        int? count = e.TryGetProperty("count", out var c) && c.ValueKind == JsonValueKind.Number ? c.GetInt32() : null;
        return new GovDataSource(Str("id") ?? "", Str("name") ?? "", Str("provider") ?? "", Str("download"), date, count);
    }
}

/// <summary>School zones and level crossings on the card for one state or New Zealand (make_card's
/// "alerts" event, after the build).</summary>
public sealed record AlertState(string Code, bool OfficialZones, int Zones, int ZoneEntries, bool OfficialCrossings,
    int Crossings)
{
    public static AlertState FromJson(JsonElement e)
    {
        static int N(JsonElement o, string n) => o.TryGetProperty(n, out var v) && v.ValueKind == JsonValueKind.Number ? v.GetInt32() : 0;
        static bool Gov(JsonElement o) => o.TryGetProperty("source", out var v) && (v.GetString() ?? "").Contains("gov");
        var z = e.GetProperty("school_zones");
        var x = e.GetProperty("crossings");
        return new AlertState(e.GetProperty("code").GetString() ?? "", Gov(z), N(z, "zones"), N(z, "points"), Gov(x),
            N(x, "crossings"));
    }
}

/// <summary>What the build used for school zones, level crossings and New Zealand's addresses and
/// suburbs, as the build step tells it.</summary>
public sealed record GovDataReport(IReadOnlyList<GovDataSource> Sources, IReadOnlyList<AlertState> States);

public static class RoadAlerts
{
    /// <summary>Mirrors the engine's openlion/alerts.py SOURCES.</summary>
    public static readonly IReadOnlyList<string> OfficialSchoolZones = ["NSW"];
    public static readonly IReadOnlyList<string> OfficialCrossings = ["NSW", "WA"];

    static string Places(IEnumerable<string> codes) => SpeedCameras.JoinAnd(codes.Select(SpeedCameras.Place).ToList());

    static string Date(DateOnly d) => d.ToString("d MMMM yyyy", CultureInfo.InvariantCulture);

    /// <summary>Before a build: where the warnings and New Zealand's addresses will come from.</summary>
    public static string Preview(IEnumerable<MapCountry> countries)
    {
        var list = countries.ToList();
        var parts = new List<string>();
        if (list.Any(c => c.Code == "AU"))
            parts.Add($"School zones for {Places(OfficialSchoolZones)} and level crossings for {Places(OfficialCrossings)} " +
                      "are downloaded fresh from the official lists when the build starts. Everywhere else they come " +
                      "from OpenStreetMap, and OpenStreetMap's level crossings are used everywhere too.");
        if (list.Any(c => c.Code == "NZ"))
            parts.Add("New Zealand's house numbers and suburbs come from LINZ, the official address and suburb " +
                      "lists (downloaded once a month: the first time takes a few minutes).");
        return string.Join(" ", parts);
    }

    /// <summary>During and after a build: what the engine actually used.</summary>
    public static string Describe(GovDataReport report)
    {
        var lines = new List<string>();
        foreach (var s in report.Sources.Where(s => s.DownloadFailed))
            lines.Add(s.Usable
                ? $"The {s.Name} list couldn't be downloaded this time, so the copy from {Date(s.Fetched!.Value)} is used."
                : $"The {s.Name} list couldn't be downloaded, so OpenStreetMap's data is used instead. " +
                  "Build again later to try again.");
        var nz = report.Sources.Where(s => s.IsNewZealand && s.Usable).ToList();
        if (nz.Count > 0)
        {
            var addr = nz.FirstOrDefault(s => s.Id == "NZ_addresses");
            var sub = nz.FirstOrDefault(s => s.Id == "NZ_suburbs");
            var bits = new List<string>();
            if (addr?.Count is { } a)
                bits.Add($"{a:N0} addresses");
            if (sub?.Count is { } b)
                bits.Add($"{b:N0} suburbs and localities");
            if (bits.Count > 0)
                lines.Add($"New Zealand: {SpeedCameras.JoinAnd(bits)} from LINZ " +
                          $"(downloaded {Date(nz.Max(s => s.Fetched!.Value))}).");
        }
        if (report.States.Count > 0)
        {
            int zones = report.States.Sum(s => s.Zones), entries = report.States.Sum(s => s.ZoneEntries);
            int rail = report.States.Sum(s => s.Crossings);
            lines.Add($"{zones:N0} school zones ({entries:N0} warnings where roads enter them) and {rail:N0} level " +
                      "crossing warnings on the card (one per direction).");
        }
        return string.Join(Environment.NewLine, lines);
    }
}
