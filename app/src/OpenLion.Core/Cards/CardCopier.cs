using System.Security.Cryptography;

namespace OpenLion.Core.Cards;

public sealed record CopyProgress(long BytesDone, long BytesTotal, int FilesDone, int FilesTotal, string CurrentFile);

public sealed record CopyResult(string CopyRoot, string NavigationPath, string ManifestPath,
    IReadOnlyList<ManifestEntry> Manifest, long TotalBytes);

public class CopyException(string message, Exception? inner = null) : IOException(message, inner);

/// <summary>
/// Copies a card's navigation/ folder to a working folder on this PC and proves the copy is
/// exact. The card is only ever opened for reading (FileAccess.Read, FileShare.Read).
/// </summary>
public static class CardCopier
{
    const int BufferSize = 1 << 20;
    const long FreeSpaceMargin = 256L << 20;

    /// <param name="cardRoot">Root of the card, e.g. "E:\". Read only.</param>
    /// <param name="copyRoot">Folder that will hold navigation/ and manifest.tsv. Replaced if it exists.</param>
    public static CopyResult Copy(string cardRoot, string copyRoot, IProgress<CopyProgress>? progress = null,
        CancellationToken ct = default) => Copy(cardRoot, copyRoot, progress, ct, allowSameDrive: false);

    /// <summary>allowSameDrive exists only so tests can copy between two temp folders.</summary>
    internal static CopyResult Copy(string cardRoot, string copyRoot, IProgress<CopyProgress>? progress,
        CancellationToken ct, bool allowSameDrive)
    {
        var src = Path.GetFullPath(Path.Combine(cardRoot, "navigation"));
        copyRoot = Path.GetFullPath(copyRoot);
        if (!Directory.Exists(src))
            throw new CopyException($"No navigation folder found on {cardRoot}.");
        if (!allowSameDrive && SameRoot(src, copyRoot))
            throw new CopyException("The copy must go to a different drive than the card."); // never write to the card
        if (IsInside(copyRoot, src) || IsInside(src, copyRoot))
            throw new CopyException("The copy folder and the card's navigation folder must not contain each other.");

        var opts = new EnumerationOptions { RecurseSubdirectories = true, AttributesToSkip = 0, IgnoreInaccessible = false };
        var files = Directory.EnumerateFiles(src, "*", opts).Order(StringComparer.Ordinal).ToList();
        long total = files.Sum(f => new FileInfo(f).Length);

        Directory.CreateDirectory(copyRoot);
        var free = new DriveInfo(Path.GetPathRoot(copyRoot)!).AvailableFreeSpace;
        if (free < total + FreeSpaceMargin)
            throw new CopyException(
                $"Not enough free disk space: the card needs {total / 1e9:0.0} GB, {free / 1e9:0.0} GB is free.");

        // Copy into a .partial folder and rename at the end, so a half-finished copy is never used.
        var partial = copyRoot + ".partial";
        if (Directory.Exists(partial))
            Directory.Delete(partial, true);
        var dstNav = Path.Combine(partial, "navigation");
        Directory.CreateDirectory(dstNav);
        var dirs = Directory.EnumerateDirectories(src, "*", opts).ToList();
        foreach (var dir in dirs) // includes empty folders, which the card has too
            Directory.CreateDirectory(Path.Combine(dstNav, Path.GetRelativePath(src, dir)));

        var manifest = new List<ManifestEntry>(files.Count);
        var buf = new byte[BufferSize];
        long done = 0;
        for (int i = 0; i < files.Count; i++)
        {
            ct.ThrowIfCancellationRequested();
            var rel = Path.GetRelativePath(src, files[i]);
            var relSlash = "navigation/" + rel.Replace('\\', '/');
            progress?.Report(new CopyProgress(done, total, i, files.Count, relSlash));
            var dst = Path.Combine(dstNav, rel);
            Directory.CreateDirectory(Path.GetDirectoryName(dst)!);

            var info = new FileInfo(files[i]);
            string srcHash;
            long size = 0;
            using (var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256))
            using (var r = new FileStream(files[i], FileMode.Open, FileAccess.Read, FileShare.Read, BufferSize, FileOptions.SequentialScan))
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
                    progress?.Report(new CopyProgress(done, total, i, files.Count, relSlash));
                }
                srcHash = Convert.ToHexStringLower(hash.GetHashAndReset());
            }
            File.SetCreationTimeUtc(dst, info.CreationTimeUtc);
            File.SetLastWriteTimeUtc(dst, info.LastWriteTimeUtc);

            if (size != info.Length)
                throw new CopyException($"{relSlash}: read {size:N0} bytes, the card says {info.Length:N0}. Is the card reader working?");
            var dstHash = Sha256File(dst, buf);
            if (dstHash != srcHash)
                throw new CopyException($"{relSlash}: the copy on this PC doesn't match the card. Your disk may be failing.");
            var mtime = info.LastWriteTime;
            mtime = mtime.AddTicks(-(mtime.Ticks % TimeSpan.TicksPerSecond)); // whole seconds, as in the TSV
            manifest.Add(new ManifestEntry(relSlash, size, mtime, srcHash));
        }

        // Folder timestamps too, so the card written later looks like the factory one.
        foreach (var dir in dirs.Prepend(src))
        {
            var d = Path.Combine(dstNav, Path.GetRelativePath(src, dir));
            Directory.SetLastWriteTimeUtc(d, Directory.GetLastWriteTimeUtc(dir));
            Directory.SetCreationTimeUtc(d, Directory.GetCreationTimeUtc(dir));
        }

        var manifestPath = Path.Combine(partial, "manifest.tsv");
        FileManifest.Write(manifestPath, manifest);

        if (Directory.Exists(copyRoot))
            Directory.Delete(copyRoot, true);
        Directory.Move(partial, copyRoot);
        progress?.Report(new CopyProgress(total, total, files.Count, files.Count, ""));
        return new CopyResult(copyRoot, Path.Combine(copyRoot, "navigation"), Path.Combine(copyRoot, "manifest.tsv"),
            manifest, total);
    }

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

    static bool IsInside(string path, string folder)
    {
        var f = folder.TrimEnd('\\') + "\\";
        return (path.TrimEnd('\\') + "\\").StartsWith(f, StringComparison.OrdinalIgnoreCase);
    }

    static bool SameRoot(string a, string b) =>
        string.Equals(Path.GetPathRoot(a)?.TrimEnd('\\'), Path.GetPathRoot(b)?.TrimEnd('\\'), StringComparison.OrdinalIgnoreCase);
}
