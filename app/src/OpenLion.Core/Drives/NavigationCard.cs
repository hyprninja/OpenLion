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

    /// <summary>Name to show the user, e.g. "MY15 (CD_8217)".</summary>
    public string? DisplayName => CardNames.Display(Volume);

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

    /// <summary>Reads navigation/cdrom.txt under <paramref name="root"/>, or, on cards without one
    /// (MY15, MY17), the db_*/cdrom.toc files. Returns null if there are none.</summary>
    public static NavigationCard? TryRead(string root)
    {
        var nav = Path.Combine(root, Folder);
        var txt = Path.Combine(nav, "cdrom.txt");
        if (File.Exists(txt))
            return Parse(ReadText(txt), nav);
        if (!Directory.Exists(nav))
            return null;
        var tocs = Directory.EnumerateDirectories(nav, "db_*")
            .Order(StringComparer.OrdinalIgnoreCase)
            .Select(d => (Folder: Path.GetFileName(d), Toc: Path.Combine(d, "cdrom.toc")))
            .Where(t => File.Exists(t.Toc))
            .Select(t => (t.Folder, Text: ReadText(t.Toc)))
            .ToList();
        return tocs.Count == 0 ? null : ParseTocs(tocs, nav);
    }

    static string ReadText(string path)
    {
        using var fs = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        using var r = new StreamReader(fs, Encoding.Latin1);
        return r.ReadToEnd();
    }

    static IEnumerable<(string Key, string Value)> KeyValues(string text)
    {
        foreach (var raw in text.Split('\n'))
        {
            var line = raw.Trim();
            int c = line.IndexOf(':');
            if (c > 0)
                yield return (line[..c].Trim(), line[(c + 1)..].Trim());
        }
    }

    /// <summary>Parses db_xxx/cdrom.toc files ("Volume:", "Customer Version:", "db:", "Area:",
    /// "Version:" lines). The card's volume is the first database's.</summary>
    public static NavigationCard ParseTocs(IReadOnlyList<(string Folder, string Text)> tocs, string navigationPath)
    {
        string? volume = null, customerVersion = null;
        var dbs = new List<MapDatabase>();
        foreach (var (folder, text) in tocs)
        {
            var kv = KeyValues(text).GroupBy(p => p.Key, StringComparer.OrdinalIgnoreCase)
                .ToDictionary(g => g.Key, g => g.First().Value, StringComparer.OrdinalIgnoreCase);
            volume ??= kv.GetValueOrDefault("Volume");
            customerVersion ??= kv.GetValueOrDefault("Customer Version");
            dbs.Add(new MapDatabase(folder, kv.GetValueOrDefault("db") ?? "", kv.GetValueOrDefault("Area"),
                kv.GetValueOrDefault("Version"), Directory.Exists(Path.Combine(navigationPath, folder))));
        }
        return new NavigationCard
        {
            NavigationPath = navigationPath,
            Volume = volume,
            CustomerVersion = customerVersion,
            Databases = dbs,
        };
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
