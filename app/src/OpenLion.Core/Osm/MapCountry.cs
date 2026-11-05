namespace OpenLion.Core.Osm;

/// <summary>A country OpenLion can put on the card (mirrors openlion/countries.py).</summary>
/// <param name="Code">ISO code, also what the engine takes ("NZ=extract.osm.pbf").</param>
/// <param name="GeofabrikPath">Path on download.geofabrik.de, without "-latest.osm.pbf".</param>
/// <param name="ApproxMb">Rough extract size, for the choice shown to people.</param>
public sealed record MapCountry(string Code, string Title, string GeofabrikPath, int ApproxMb)
{
    public static readonly MapCountry Australia = new("AU", "Australia", "australia-oceania/australia", 970);
    public static readonly MapCountry NewZealand = new("NZ", "New Zealand", "australia-oceania/new-zealand", 410);

    /// <summary>In the card's catalog order (Australia is catalog 0, New Zealand 1).</summary>
    public static readonly IReadOnlyList<MapCountry> All = [Australia, NewZealand];

    public Uri LatestUrl => new($"https://download.geofabrik.de/{GeofabrikPath}-latest.osm.pbf");

    public static MapCountry? ByCode(string? code) =>
        All.FirstOrDefault(c => string.Equals(c.Code, code, StringComparison.OrdinalIgnoreCase));

    /// <summary>Known codes from a saved list, in catalog order; Australia when none are valid.</summary>
    public static IReadOnlyList<MapCountry> FromCodes(IEnumerable<string>? codes)
    {
        var set = (codes ?? []).Select(ByCode).OfType<MapCountry>().ToHashSet();
        var list = All.Where(set.Contains).ToList();
        return list.Count > 0 ? list : [Australia];
    }

    /// <summary>"Australia", "New Zealand" or "Australia and New Zealand".</summary>
    public static string Describe(IEnumerable<MapCountry> countries)
    {
        var t = countries.Select(c => c.Title).ToList();
        return t.Count <= 1 ? string.Concat(t) : string.Join(", ", t.Take(t.Count - 1)) + " and " + t[^1];
    }
}
