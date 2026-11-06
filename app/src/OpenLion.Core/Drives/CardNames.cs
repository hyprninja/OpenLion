using System.Text.RegularExpressions;

namespace OpenLion.Core.Drives;

/// <summary>Friendly names for the iQ card versions, for the user interface only.
/// Files, reports and records keep the card's own volume string.</summary>
public static partial class CardNames
{
    static readonly Dictionary<string, string> ModelYears = new(StringComparer.OrdinalIgnoreCase)
    {
        ["CD_7172"] = "MY10",
        ["CD_7597"] = "MY12",
        ["CD_8217"] = "MY15",
        ["CD_8418"] = "MY17",
    };

    [GeneratedRegex(@"CD_\d+", RegexOptions.IgnoreCase)]
    private static partial Regex VolumeCode();

    /// <summary>"MY12 (CD_7597)" for a known card (the first CD_ code in the volume line),
    /// otherwise the volume line as it is. Null for a null or empty volume.</summary>
    public static string? Display(string? volume)
    {
        if (string.IsNullOrWhiteSpace(volume))
            return null;
        var m = VolumeCode().Match(volume);
        if (m.Success && ModelYears.TryGetValue(m.Value, out var my))
            return $"{my} ({m.Value.ToUpperInvariant()})";
        return volume.Trim();
    }
}
