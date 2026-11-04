using System.Text.Json;
using OpenLion.Core.Drives;

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

    /// <summary>Folder for the PC copy of one factory card, named by label and volume serial.</summary>
    public static string CardCopyFolder(DriveSnapshot card)
    {
        var label = new string((card.Label.Length > 0 ? card.Label : "card")
            .Select(c => char.IsLetterOrDigit(c) || c is '-' or '_' ? c : '_').ToArray());
        return Path.Combine(CardsFolder, $"{label}_{card.VolumeSerial:X8}");
    }
}

/// <summary>Remembered between runs: the factory card that was copied and the cards OpenLion wrote.</summary>
public sealed class AppState
{
    public FactoryCardRecord? Factory { get; set; }
    public string? FactoryCopyPath { get; set; }
    public List<uint> WrittenVolumeSerials { get; set; } = [];

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
