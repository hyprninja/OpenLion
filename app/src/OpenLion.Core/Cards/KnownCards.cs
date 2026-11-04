namespace OpenLion.Core.Cards;

/// <param name="Name">Known card name, e.g. "CD_7606" (the manifest file's name).</param>
/// <param name="Exact">Every file matches the known card (path, size and SHA-256), nothing missing or extra.</param>
/// <param name="Differences">Number of files that are missing, extra or different.</param>
public sealed record KnownCardMatch(string Name, bool Exact, int Differences);

/// <summary>Cards whose full file list is known (KnownCards/*.tsv, same format as the manifest).
/// A card that matches one exactly is "verified": OpenLion has been tested on it.</summary>
public static class KnownCards
{
    public static string DefaultFolder => Path.Combine(AppContext.BaseDirectory, "KnownCards");

    /// <summary>The closest known card, or null if there are none to compare with.</summary>
    public static KnownCardMatch? Identify(IReadOnlyCollection<ManifestEntry> copy, string? folder = null)
    {
        folder ??= DefaultFolder;
        if (!Directory.Exists(folder))
            return null;
        var mine = copy.ToDictionary(e => e.Path, e => (e.Size, e.Sha256), StringComparer.OrdinalIgnoreCase);
        KnownCardMatch? best = null;
        foreach (var tsv in Directory.EnumerateFiles(folder, "*.tsv"))
        {
            var known = FileManifest.Read(tsv);
            var paths = new HashSet<string>(known.Select(k => k.Path), StringComparer.OrdinalIgnoreCase);
            int diff = known.Count(k => !mine.TryGetValue(k.Path, out var m) || m.Size != k.Size || m.Sha256 != k.Sha256)
                       + mine.Keys.Count(p => !paths.Contains(p));
            var match = new KnownCardMatch(Path.GetFileNameWithoutExtension(tsv), diff == 0, diff);
            if (best is null || match.Differences < best.Differences)
                best = match;
        }
        return best;
    }
}
