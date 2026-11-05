using System.Text.Json;
using OpenLion.Core.Drives;
using OpenLion.Core.Osm;

namespace OpenLion.Core;

/// <summary>Where OpenLion keeps its working files: %LOCALAPPDATA%\OpenLion, or the folder in the
/// OPENLION_HOME environment variable (for development and testing).</summary>
public static class AppPaths
{
    public static string Root { get; set; } =
        Environment.GetEnvironmentVariable("OPENLION_HOME") is { Length: > 0 } home
            ? home
            : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "OpenLion");

    public static string CardsFolder => Path.Combine(Root, "cards");
    public static string StateFile => Path.Combine(Root, "state.json");
    public static string OsmFolder => Path.Combine(Root, "osm");
    /// <summary>One extract per country: Australia's stays where OpenLion 1.0 kept it.</summary>
    public static string OsmFolderFor(MapCountry c) =>
        c == MapCountry.Australia ? OsmFolder : Path.Combine(OsmFolder, c.Code.ToLowerInvariant());
    /// <summary>Engine working folder: cached OSM reads and the new map tables.</summary>
    public static string BuildWorkFolder => Path.Combine(Root, "build", "work");
    /// <summary>The finished navigation/ folder that gets written to the new card.</summary>
    public static string BuildOutput => Path.Combine(Root, "build", "navigation");

    /// <summary>Folder for the PC copy of one factory card, named by label and volume serial.</summary>
    public static string CardCopyFolder(DriveSnapshot card)
    {
        var label = new string((card.Label.Length > 0 ? card.Label : "card")
            .Select(c => char.IsLetterOrDigit(c) || c is '-' or '_' ? c : '_').ToArray());
        return Path.Combine(CardsFolder, $"{label}_{card.VolumeSerial:X8}");
    }
}

/// <summary>A finished build: which factory card and OSM extracts it came from.</summary>
/// <param name="OsmFileName">The extract's file name; several countries' names joined by "+"
/// (see <see cref="OsmKey"/>).</param>
public sealed record BuildRecord(string NavigationPath, uint FactoryVolumeSerial, string OsmFileName, DateOnly? OsmDate,
    int Files, long Bytes, DateTime BuiltUtc, bool Partial)
{
    /// <summary>What a build is keyed on: the extracts' file names in country order. One
    /// Australian extract gives its plain file name, as OpenLion 1.0 stored it.</summary>
    public static string OsmKey(IEnumerable<OsmExtract> extracts) => string.Join("+", extracts.Select(e => e.FileName));

    /// <summary>Still usable: the folder exists and it was built from this card and extract.</summary>
    public bool Matches(FactoryCardRecord? factory, string? osmFileName) =>
        factory is not null && factory.VolumeSerial == FactoryVolumeSerial &&
        string.Equals(osmFileName, OsmFileName, StringComparison.OrdinalIgnoreCase) &&
        Directory.Exists(NavigationPath);
}

/// <summary>Remembered between runs: the factory card that was copied and the cards OpenLion wrote.</summary>
public sealed class AppState
{
    public FactoryCardRecord? Factory { get; set; }
    public string? FactoryCopyPath { get; set; }
    public List<uint> WrittenVolumeSerials { get; set; } = [];
    /// <summary>The last finished map build, if any.</summary>
    public BuildRecord? Build { get; set; }
    /// <summary>Countries to put on the card (MapCountry codes); null means Australia, as in 1.0.</summary>
    public List<string>? Countries { get; set; }

    static readonly JsonSerializerOptions Json = new() { WriteIndented = true };

    public static AppState Load()
    {
        try
        {
            return File.Exists(AppPaths.StateFile)
                ? JsonSerializer.Deserialize<AppState>(File.ReadAllText(AppPaths.StateFile), Json) ?? new AppState()
                : new AppState();
        }
        catch (Exception e) when (e is IOException or JsonException or UnauthorizedAccessException)
        {
            return new AppState();
        }
    }

    public void Save()
    {
        Directory.CreateDirectory(AppPaths.Root);
        var tmp = AppPaths.StateFile + ".tmp";
        File.WriteAllText(tmp, JsonSerializer.Serialize(this, Json));
        File.Move(tmp, AppPaths.StateFile, overwrite: true);
    }
}
