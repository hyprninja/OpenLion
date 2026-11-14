using System.Security.Cryptography;

namespace OpenLion.Core.Cards;

public sealed record CopyProgress(long BytesDone, long BytesTotal, int FilesDone, int FilesTotal, string CurrentFile);

/// <param name="CardRootPath">Everything else from the card's root (config/ and so on), copied as is.</param>
/// <param name="CardRootManifest">Those files, with paths relative to the card's root ("config/create_cd").</param>
public sealed record CopyResult(string CopyRoot, string NavigationPath, string ManifestPath,
    IReadOnlyList<ManifestEntry> Manifest, long TotalBytes, string CardRootPath,
    IReadOnlyList<ManifestEntry> CardRootManifest);

public class CopyException(string message, Exception? inner = null) : IOException(message, inner);

/// <summary>
/// Copies a card to a working folder on this PC and proves the copy is exact: its navigation/
/// folder, plus every other folder and file on the card's root (the factory cards have a config/
/// folder the unit needs). The card is only ever opened for reading (FileAccess.Read, FileShare.Read).
/// </summary>
public static class CardCopier
{
    const int BufferSize = 1 << 20;
    const long FreeSpaceMargin = 256L << 20;

    /// <summary>Folder in the copy that holds the card's other root items. Copies made before
    /// OpenLion 1.3 don't have it.</summary>
    public const string CardRootFolder = "card_root";
    public const string CardRootManifestName = "card_root.tsv";

    /// <summary>Root items that are never copied: the maps (copied on their own) and what
    /// Windows or macOS put on any drive.</summary>
    static readonly HashSet<string> NotCopied = new(StringComparer.OrdinalIgnoreCase)
    {
        "navigation", "System Volume Information", "$RECYCLE.BIN", "RECYCLER",
        ".Spotlight-V100", ".Trashes", ".fseventsd", "Thumbs.db", "desktop.ini",
    };

    /// <summary>Whether an item on a card's root goes along with the maps.</summary>
    public static bool IsCardRootItem(string name) =>
        !NotCopied.Contains(name) && !name.StartsWith("._", StringComparison.Ordinal);

    /// <param name="cardRoot">Root of the card, e.g. "E:\". Read only.</param>
    /// <param name="copyRoot">Folder that will hold navigation/, card_root/ and their manifests. Replaced if it exists.</param>
    public static CopyResult Copy(string cardRoot, string copyRoot, IProgress<CopyProgress>? progress = null,
        CancellationToken ct = default) => Copy(cardRoot, copyRoot, progress, ct, allowSameDrive: false);

    /// <summary>allowSameDrive exists only so tests can copy between two temp folders.</summary>
    internal static CopyResult Copy(string cardRoot, string copyRoot, IProgress<CopyProgress>? progress,
        CancellationToken ct, bool allowSameDrive)
    {
        cardRoot = Path.GetFullPath(cardRoot);
        var src = Path.GetFullPath(Path.Combine(cardRoot, "navigation"));
        copyRoot = Path.GetFullPath(copyRoot);
        if (!Directory.Exists(src))
            throw new CopyException($"No navigation folder found on {cardRoot}.");
        if (!allowSameDrive && SameRoot(src, copyRoot))
            throw new CopyException("The copy must go to a different drive than the card."); // never write to the card
        if (IsInside(copyRoot, cardRoot) || IsInside(src, copyRoot))
            throw new CopyException("The copy folder and the card must not contain each other.");

        var partial = copyRoot + ".partial";
        var dstNav = Path.Combine(partial, "navigation");
        var dstRoot = Path.Combine(partial, CardRootFolder);

        // (source, destination, manifest path, part of navigation/)
        var opts = new EnumerationOptions { RecurseSubdirectories = true, AttributesToSkip = 0, IgnoreInaccessible = false };
        var files = Directory.EnumerateFiles(src, "*", opts).Order(StringComparer.Ordinal)
            .Select(f => (src: f, dst: Path.Combine(dstNav, Path.GetRelativePath(src, f)),
                          key: "navigation/" + Slash(Path.GetRelativePath(src, f)), nav: true))
            .ToList();
        var dirs = Directory.EnumerateDirectories(src, "*", opts)
            .Select(d => (src: d, dst: Path.Combine(dstNav, Path.GetRelativePath(src, d))))
            .Prepend((src, dstNav)).ToList();

        var top = new EnumerationOptions { RecurseSubdirectories = false, AttributesToSkip = 0, IgnoreInaccessible = false };
        foreach (var item in Directory.EnumerateFileSystemEntries(cardRoot, "*", top).Order(StringComparer.Ordinal))
        {
            if (!IsCardRootItem(Path.GetFileName(item)))
                continue;
            if (Directory.Exists(item))
            {
                dirs.AddRange(Directory.EnumerateDirectories(item, "*", opts).Prepend(item)
                    .Select(d => (src: d, dst: Path.Combine(dstRoot, Path.GetRelativePath(cardRoot, d)))));
                files.AddRange(Directory.EnumerateFiles(item, "*", opts).Order(StringComparer.Ordinal)
                    .Select(f => (src: f, dst: Path.Combine(dstRoot, Path.GetRelativePath(cardRoot, f)),
                                  key: Slash(Path.GetRelativePath(cardRoot, f)), nav: false)));
            }
            else
                files.Add((item, Path.Combine(dstRoot, Path.GetFileName(item)), Path.GetFileName(item), false));
        }
        long total = files.Sum(f => new FileInfo(f.src).Length);

        Directory.CreateDirectory(copyRoot);
        var free = new DriveInfo(Path.GetPathRoot(copyRoot)!).AvailableFreeSpace;
        if (free < total + FreeSpaceMargin)
            throw new CopyException(
                $"Not enough free disk space: the card needs {total / 1e9:0.0} GB, {free / 1e9:0.0} GB is free.");

        // Copy into a .partial folder and rename at the end, so a half-finished copy is never used.
        if (Directory.Exists(partial))
            Directory.Delete(partial, true);
        Directory.CreateDirectory(dstRoot); // always there, even when the card has nothing else: marks a 1.3+ copy
        foreach (var (_, dst) in dirs) // includes empty folders, which the card has too
            Directory.CreateDirectory(dst);

        var manifest = new List<ManifestEntry>(files.Count);
        var rootManifest = new List<ManifestEntry>();
        var buf = new byte[BufferSize];
        long done = 0;
        for (int i = 0; i < files.Count; i++)
        {
            ct.ThrowIfCancellationRequested();
            var (from, dst, key, nav) = files[i];
            progress?.Report(new CopyProgress(done, total, i, files.Count, key));
            Directory.CreateDirectory(Path.GetDirectoryName(dst)!);

            var info = new FileInfo(from);
            string srcHash;
            long size = 0;
            using (var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256))
            using (var r = new FileStream(from, FileMode.Open, FileAccess.Read, FileShare.Read, BufferSize, FileOptions.SequentialScan))
            using (var w = new FileStream(dst, FileMode.CreateNew, FileAccess.Write, FileShare.None, BufferSize))
            {
                int n;
                while ((n = r.Read(buf, 0, buf.Length)) > 0)
                {
                    ct.ThrowIfCancellationRequested();
                    hash.AppendData(buf, 0, n);
                    w.Write(buf, 0, n);
                    size += n;
                    done += n;
                    progress?.Report(new CopyProgress(done, total, i, files.Count, key));
                }
                srcHash = Convert.ToHexStringLower(hash.GetHashAndReset());
            }
            File.SetCreationTimeUtc(dst, info.CreationTimeUtc);
            File.SetLastWriteTimeUtc(dst, info.LastWriteTimeUtc);

            if (size != info.Length)
                throw new CopyException($"{key}: read {size:N0} bytes, the card says {info.Length:N0}. Is the card reader working?");
            var dstHash = Sha256File(dst, buf);
            if (dstHash != srcHash)
                throw new CopyException($"{key}: the copy on this PC doesn't match the card. Your disk may be failing.");
            var mtime = info.LastWriteTime;
            mtime = mtime.AddTicks(-(mtime.Ticks % TimeSpan.TicksPerSecond)); // whole seconds, as in the TSV
            (nav ? manifest : rootManifest).Add(new ManifestEntry(key, size, mtime, srcHash));
        }

        // Folder timestamps too, so the card written later looks like the factory one.
        foreach (var (dir, d) in dirs)
        {
            Directory.SetLastWriteTimeUtc(d, Directory.GetLastWriteTimeUtc(dir));
            Directory.SetCreationTimeUtc(d, Directory.GetCreationTimeUtc(dir));
        }

        FileManifest.Write(Path.Combine(partial, "manifest.tsv"), manifest);
        FileManifest.Write(Path.Combine(partial, CardRootManifestName), rootManifest);

        if (Directory.Exists(copyRoot))
            Directory.Delete(copyRoot, true);
        Directory.Move(partial, copyRoot);
        progress?.Report(new CopyProgress(total, total, files.Count, files.Count, ""));
        return new CopyResult(copyRoot, Path.Combine(copyRoot, "navigation"), Path.Combine(copyRoot, "manifest.tsv"),
            manifest, total, Path.Combine(copyRoot, CardRootFolder), rootManifest);
    }

    /// <summary>The card_root/ folder of a factory copy, or null for a copy made before OpenLion 1.3.</summary>
    public static string? CardRootOf(string? copyRoot) =>
        copyRoot is not null && Directory.Exists(Path.Combine(copyRoot, CardRootFolder))
            ? Path.Combine(copyRoot, CardRootFolder)
            : null;

    public static string Sha256File(string path, byte[]? buffer = null)
    {
        buffer ??= new byte[BufferSize];
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        using var r = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, buffer.Length, FileOptions.SequentialScan);
        int n;
        while ((n = r.Read(buffer, 0, buffer.Length)) > 0)
            hash.AppendData(buffer, 0, n);
        return Convert.ToHexStringLower(hash.GetHashAndReset());
    }

    static string Slash(string rel) => rel.Replace('\\', '/');

#if WINDOWS
    static bool IsInside(string path, string folder)
    {
        var f = folder.TrimEnd('\\') + "\\";
        return (path.TrimEnd('\\') + "\\").StartsWith(f, StringComparison.OrdinalIgnoreCase);
    }

    static bool SameRoot(string a, string b) =>
        string.Equals(Path.GetPathRoot(a)?.TrimEnd('\\'), Path.GetPathRoot(b)?.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase);
#else
    // macOS: one folder tree, so "the same drive" means the same mount point. Compared without case,
    // as macOS volumes usually are: a false "inside" or "same drive" only refuses a copy.
    internal static bool IsInside(string path, string folder)
    {
        var f = folder.TrimEnd('/') + "/";
        return (path.TrimEnd('/') + "/").StartsWith(f, StringComparison.OrdinalIgnoreCase);
    }

    internal static bool SameRoot(string a, string b) =>
        string.Equals(MountPointOf(a), MountPointOf(b), StringComparison.OrdinalIgnoreCase);

    /// <summary>The mount point a path is on: the longest one that contains it ("/Volumes/CARD", or "/").</summary>
    internal static string MountPointOf(string path, IEnumerable<string>? mounts = null)
    {
        var full = Path.GetFullPath(path);
        return (mounts ?? DriveInfo.GetDrives().Select(d => d.Name))
            .Where(m => m == "/" || IsInside(full, m))
            .OrderByDescending(m => m.Length)
            .FirstOrDefault() ?? "/";
    }
#endif
}
