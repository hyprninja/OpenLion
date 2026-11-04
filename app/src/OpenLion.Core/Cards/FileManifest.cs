using System.Globalization;
using System.Text;

namespace OpenLion.Core.Cards;

/// <summary>One file of a card: path relative to the card root ("navigation/..."), size,
/// last-write time (local, as FAT stores it) and SHA-256. Same TSV layout as
/// docs/reference/manifest_sha256.tsv.</summary>
public sealed record ManifestEntry(string Path, long Size, DateTime MTime, string Sha256);

public static class FileManifest
{
    const string Header = "path\tsize\tmtime\tsha256";
    const string TimeFormat = "yyyy-MM-dd HH:mm:ss";

    public static void Write(string tsvPath, IEnumerable<ManifestEntry> entries)
    {
        var sb = new StringBuilder(Header).Append('\n');
        foreach (var e in entries.OrderBy(e => e.Path, StringComparer.Ordinal))
            sb.Append(e.Path).Append('\t').Append(e.Size.ToString(CultureInfo.InvariantCulture)).Append('\t')
              .Append(e.MTime.ToString(TimeFormat, CultureInfo.InvariantCulture)).Append('\t')
              .Append(e.Sha256).Append('\n');
        File.WriteAllText(tsvPath, sb.ToString(), new UTF8Encoding(false));
    }

    public static List<ManifestEntry> Read(string tsvPath) => Parse(File.ReadAllLines(tsvPath, Encoding.UTF8));

    public static List<ManifestEntry> Parse(IEnumerable<string> lines)
    {
        var list = new List<ManifestEntry>();
        foreach (var line in lines.Skip(1))
        {
            var f = line.Split('\t');
            if (f.Length < 4 || f[3].Length != 64)
                continue; // directory rows or blank lines
            list.Add(new ManifestEntry(f[0], long.Parse(f[1], CultureInfo.InvariantCulture),
                DateTime.ParseExact(f[2], TimeFormat, CultureInfo.InvariantCulture), f[3].ToLowerInvariant()));
        }
        return list;
    }
}
