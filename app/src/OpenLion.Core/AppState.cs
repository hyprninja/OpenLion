using System.Text.Json;
using OpenLion.Core.Drives;
using OpenLion.Core.Osm;

namespace OpenLion.Core;

/// <summary>Where OpenLion keeps its working files: %LOCALAPPDATA%\OpenLion. A developer build
/// (no bundled engine) may use the folder in the OPENLION_HOME environment variable instead;
/// an installed release ignores it.</summary>
public static class AppPaths
{
    public static string Root { get; set; } = Path.GetFullPath(
        !Engine.EngineLocator.HasBundledEngine() &&
        Environment.GetEnvironmentVariable("OPENLION_HOME") is { Length: > 0 } home
            ? home
            : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "OpenLion"));

    /// <summary>Throws unless <paramref name="path"/> is strictly inside <paramref name="parent"/>.
    /// Guards every recursive delete of a folder the app manages.</summary>
    public static string EnsureInside(string path, string parent)
    {
        var full = Path.GetFullPath(path).TrimEnd(Path.DirectorySeparatorChar);
        var root = Path.GetFullPath(parent).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        if (!full.StartsWith(root, StringComparison.OrdinalIgnoreCase))
            throw new InvalidOperationException($"Refusing to change {full}: it is not inside {root}");
        return full;
    }

    public static string CardsFolder => Path.Combine(Root, "cards");
    public static string StateFile => Path.Combine(Root, "state.json");
    public static string OsmFolder => Path.Combine(Root, "osm");
    /// <summary>One extract per country: Australia's stays where OpenLion 1.0 kept it.</summary>
    public static string OsmFolderFor(MapCountry c) =>
        c == MapCountry.Australia ? OsmFolder : Path.Combine(OsmFolder, c.Code.ToLowerInvariant());
    /// <summary>One folder per state's prepared speed zones (speed/vic, ...).</summary>
    public static string SpeedZonesFolderFor(Speed.SpeedZoneSource s) =>
        Path.Combine(Root, "speed", s.Code.ToLowerInvariant());
    /// <summary>Official speed camera lists, downloaded by the engine at each build (VIC.json, ...).</summary>
    public static string CamerasFolder => Path.Combine(Root, "cameras");
    /// <summary>The G-NAF release (house numbers), one zip at a time.</summary>
    public static string GnafFolder => Path.Combine(Root, "gnaf");
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
/// <param name="CameraNote">What the build said about its speed camera sources (SpeedCameras.Describe).</param>
public sealed record BuildRecord(string NavigationPath, uint FactoryVolumeSerial, string OsmFileName, DateOnly? OsmDate,
    int Files, long Bytes, DateTime BuiltUtc, bool Partial, string? CameraNote = null)
{
    /// <summary>What a build is keyed on: the extracts' file names in country order. One
    /// Australian extract gives its plain file name, as OpenLion 1.0 stored it.</summary>
    public static string OsmKey(IEnumerable<OsmExtract> extracts) => string.Join("+", extracts.Select(e => e.FileName));

    /// <summary><see cref="OsmKey"/> plus the speed zone data and G-NAF release used, if any
    /// ("...|speed:VIC=Speed Zones September 2026|gnaf:g-naf_aug26_....zip"), so a build is redone when
    /// either changes. Without them it's the plain OSM key, as earlier versions stored it.</summary>
    public static string Key(IEnumerable<OsmExtract> extracts, IEnumerable<Speed.SpeedZoneData>? speedZones,
        Addresses.GnafData? gnaf = null)
    {
        var key = OsmKey(extracts);
        var z = (speedZones ?? []).OrderBy(d => d.Code, StringComparer.Ordinal).ToList();
        if (z.Count > 0)
            key += "|speed:" + string.Join(",", z.Select(d => $"{d.Code}={d.SourceFile}"));
        if (gnaf is not null)
            key += "|gnaf:" + gnaf.FileName;
        return key;
    }

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

    /// <summary>The factory card copied in an earlier run, when its verified copy is still on this PC
    /// and was made by OpenLion 1.3 or later (older copies left out config/ and the card's other
    /// root folders); otherwise null.</summary>
    public FactoryCardRecord? UsableFactoryCopy() =>
        Factory is { CopyVerified: true } f && FactoryCopyPath is { } p &&
        Directory.Exists(Path.Combine(p, "navigation")) && Cards.CardCopier.CardRootOf(p) is not null
            ? f
            : null;
    public List<uint> WrittenVolumeSerials { get; set; } = [];
    /// <summary>The last finished map build, if any.</summary>
    public BuildRecord? Build { get; set; }
    /// <summary>Countries to put on the card (MapCountry codes); null means Australia, as in 1.0.</summary>
    public List<string>? Countries { get; set; }
    /// <summary>States whose speed zone data goes on the card (SpeedZoneSource codes); null means none.</summary>
    public List<string>? SpeedZones { get; set; }
    /// <summary>Add house numbers from G-NAF (Australia); null means yes, the default.</summary>
    public bool? HouseNumbers { get; set; }

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
