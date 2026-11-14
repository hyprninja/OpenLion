namespace OpenLion.Core;

/// <summary>Words that differ between the Windows and macOS apps, so shared messages read right on both.</summary>
public static class Os
{
    public static bool IsMac => OperatingSystem.IsMacOS();

    /// <summary>"Windows" or "macOS".</summary>
    public static string Name => IsMac ? "macOS" : "Windows";

    /// <summary>"PC" or "Mac", as in "copy your card to this PC".</summary>
    public static string Computer => IsMac ? "Mac" : "PC";

    /// <summary>A disk in a sentence ("Only 2 GB free on E:"): the drive on Windows, this Mac's disk on macOS,
    /// where everything is in one folder tree.</summary>
    public static string DiskName(string root) => IsMac ? "this Mac's disk" : root.TrimEnd('\\');
}
