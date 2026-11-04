using System.Text;

namespace OpenLion.Core.Drives;

/// <summary>A map database on the card, e.g. db_aunz = "AUNZ(2010Q3)".</summary>
public sealed record MapDatabase(string Folder, string Name, string? Area, string? Version, bool FolderExists);

/// <summary>What navigation/cdrom.txt says about a card. Read-only: only opens files for reading.</summary>
public sealed record NavigationCard
{
    public required string NavigationPath { get; init; }
    public string? Volume { get; init; }
    public string? CustomerVersion { get; init; }
    public IReadOnlyList<MapDatabase> Databases { get; init; } = [];

    public const string Folder = "navigation";
    static readonly string[] Markers = ["cdrom.txt", "cdrom.toc"];

    /// <summary>True if the drive has the iQ navigation metadata files. Never throws.</summary>
    public static bool HasMarkers(string root)
    {
        try
        {
            var nav = Path.Combine(root, Folder);
            return Markers.Any(m => File.Exists(Path.Combine(nav, m))) ||
                   Directory.Exists(nav) && Directory.EnumerateDirectories(nav, "db_*")
                       .Any(d => File.Exists(Path.Combine(d, "cdrom.toc")));
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return false;
        }
    }

    /// <summary>Reads navigation/cdrom.txt under <paramref name="root"/>. Returns null if there is none.</summary>
    public static NavigationCard? TryRead(string root)
    {
        var nav = Path.Combine(root, Folder);
        var txt = Path.Combine(nav, "cdrom.txt");
        if (!File.Exists(txt))
            return null;
        string text;
        using (var fs = new FileStream(txt, FileMode.Open, FileAccess.Read, FileShare.Read))
        using (var r = new StreamReader(fs, Encoding.Latin1))
            text = r.ReadToEnd();
        return Parse(text, nav);
    }

    /// <summary>Parses cdrom.txt: "Key:   value" lines; each "db_xxx:" line starts a database block
    /// whose following Area/Version lines belong to it.</summary>
    public static NavigationCard Parse(string text, string navigationPath)
    {
        string? volume = null, customerVersion = null;
        var dbs = new List<MapDatabase>();
        string? dbFolder = null, dbName = null, area = null, version = null;

        void Flush()
        {
            if (dbFolder is not null)
                dbs.Add(new MapDatabase(dbFolder, dbName ?? "", area, version,
                    Directory.Exists(Path.Combine(navigationPath, dbFolder))));
            dbFolder = dbName = area = version = null;
        }

        foreach (var raw in text.Split('\n'))
        {
            var line = raw.Trim();
            int c = line.IndexOf(':');
            if (c <= 0)
                continue;
            var key = line[..c].Trim();
            var val = line[(c + 1)..].Trim();
            if (key.Equals("Volume", StringComparison.OrdinalIgnoreCase)) volume = val;
            else if (key.Equals("Customer Version", StringComparison.OrdinalIgnoreCase)) customerVersion = val;
            else if (key.StartsWith("db_", StringComparison.OrdinalIgnoreCase) && !key.Contains(' '))
            {
                Flush();
                dbFolder = key;
                dbName = val;
            }
            else if (dbFolder is not null && key.Equals("Area", StringComparison.OrdinalIgnoreCase)) area = val;
            else if (dbFolder is not null && key.Equals("Version", StringComparison.OrdinalIgnoreCase)) version = val;
        }
        Flush();
        return new NavigationCard
        {
            NavigationPath = navigationPath,
            Volume = volume,
            CustomerVersion = customerVersion,
            Databases = dbs,
        };
    }
}
