using System.Runtime.InteropServices;
using System.Security.Cryptography;
using Microsoft.Win32.SafeHandles;
using OpenLion.Core.Drives;

namespace OpenLion.Core.Cards;

public enum WritePhase { Checking, Writing, Verifying, Finishing }

public sealed record WriteProgress(WritePhase Phase, int FilesDone, int FilesTotal, long BytesDone, long BytesTotal);

/// <param name="LabelWarning">Set when the factory card's volume label couldn't be copied.</param>
public sealed record WriteResult(string NavigationPath, int Files, long Bytes, uint VolumeSerial, string? LabelWarning);

public class WriteException(string message, Exception? inner = null) : IOException(message, inner);

/// <summary>
/// Writes a finished navigation/ folder, and the factory card's other root items (config/ and
/// so on), to a blank card. Every write goes through
/// <see cref="CardGuard"/> again right before it starts (via the gate), so a card swapped
/// in the meantime is refused. Files go to navigation.partial (write-through), every file is
/// then read back from the card itself, bypassing Windows' cache, and compared by SHA-256;
/// only then is the folder renamed to navigation. The other root items go to openlion.partial
/// first, the same way, and are moved to the card's root at the end.
/// </summary>
public static partial class CardWriter
{
    public const string FolderName = "navigation";
    const string PartialName = "navigation.partial";
    const string RootPartialName = "openlion.partial";
    const long SpaceMargin = 16_000_000;
    const FileOptions NoBuffering = (FileOptions)0x20000000; // FILE_FLAG_NO_BUFFERING
    const int Block = 1 << 20;

    [LibraryImport("kernel32.dll", EntryPoint = "SetVolumeLabelW", SetLastError = true, StringMarshalling = StringMarshalling.Utf16)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool SetVolumeLabel(string root, string? label);

    /// <param name="source">The built navigation/ folder.</param>
    /// <param name="target">The card, as the user chose it.</param>
    /// <param name="gate">Re-checks the card right before writing; must return an allowed result
    /// for the same volume. Throws or returns refused to stop.</param>
    /// <param name="label">Volume label to give the card (the factory card's), or null to leave it.</param>
    /// <param name="cardRoot">The factory card's other root items (the copy's card_root/ folder), written
    /// to the card's root next to navigation/; null for none.</param>
    public static WriteResult Write(string source, DriveSnapshot target, Func<GuardResult> gate, string? label,
        IProgress<WriteProgress>? progress = null, CancellationToken ct = default, string? cardRoot = null)
    {
        progress?.Report(new WriteProgress(WritePhase.Checking, 0, 0, 0, 0));
        var check = gate();
        if (!check.Allowed)
            throw new WriteException("The card was refused: " + string.Join(" ", check.Findings.Select(f => f.Message)));
        if (!SameVolume(check.Target, target))
            throw new WriteException("A different card is now at this drive letter. Nothing was written.");
        var root = target.Root;
        if (!Directory.Exists(source))
            throw new WriteException($"The built maps folder is missing: {source}");

        if (cardRoot is not null && !Directory.Exists(cardRoot))
            throw new WriteException($"The factory card's other folders are missing from the copy: {cardRoot}");

        var partial = Path.Combine(root, PartialName);
        var rootPartial = Path.Combine(root, RootPartialName);
        var final = Path.Combine(root, FolderName);
        var files = Files(source, partial, FolderName);
        var dirs = Dirs(source, partial);
        var extras = cardRoot is null ? [] : Directory.EnumerateFileSystemEntries(cardRoot).Select(Path.GetFileName).OfType<string>().ToList();
        if (cardRoot is not null)
        {
            files.AddRange(Files(cardRoot, rootPartial, null));
            dirs.AddRange(Dirs(cardRoot, rootPartial));
        }
        long total = files.Sum(f => f.size);

        // Only reachable for an empty card, one OpenLion wrote before, or one whose files the user
        // agreed to lose (CardGuard): old maps and old copies of the root items go.
        foreach (var d in new[] { partial, rootPartial, final })
            if (Directory.Exists(d))
                Directory.Delete(d, true);
        foreach (var name in extras)
            DeleteItem(Path.Combine(root, name));
        long free = new DriveInfo(root).AvailableFreeSpace;
        if (free < total + SpaceMargin)
            throw new WriteException($"Not enough space on the card: {total / 1e6:N0} MB needed, {free / 1e6:N0} MB free.");

        // 1. write
        Directory.CreateDirectory(partial);
        if (cardRoot is not null)
            Directory.CreateDirectory(rootPartial);
        foreach (var (_, d) in dirs)
            Directory.CreateDirectory(d);
        var hashes = new Dictionary<string, byte[]>(StringComparer.OrdinalIgnoreCase);
        var buf = new byte[Block];
        long done = 0;
        int n = 0;
        foreach (var (path, dst, rel, _) in files)
        {
            ct.ThrowIfCancellationRequested();
            using (var sha = IncrementalHash.CreateHash(HashAlgorithmName.SHA256))
            {
                using (var i = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read, Block, FileOptions.SequentialScan))
                using (var o = new FileStream(dst, FileMode.CreateNew, FileAccess.Write, FileShare.None, Block, FileOptions.WriteThrough))
                {
                    int r;
                    while ((r = i.Read(buf, 0, buf.Length)) > 0)
                    {
                        ct.ThrowIfCancellationRequested();
                        sha.AppendData(buf, 0, r);
                        o.Write(buf, 0, r);
                        done += r;
                        progress?.Report(new WriteProgress(WritePhase.Writing, n, files.Count, done, total));
                    }
                    o.Flush(flushToDisk: true);
                }
                hashes[rel] = sha.GetHashAndReset();
            }
            File.SetCreationTimeUtc(dst, File.GetCreationTimeUtc(path));
            File.SetLastWriteTimeUtc(dst, File.GetLastWriteTimeUtc(path));
            n++;
            progress?.Report(new WriteProgress(WritePhase.Writing, n, files.Count, done, total));
        }
        foreach (var (src, d) in dirs.OrderByDescending(d => d.dst.Length))
            Directory.SetLastWriteTimeUtc(d, Directory.GetLastWriteTimeUtc(src));

        // 2. read every file back from the card and compare
        done = 0;
        n = 0;
        foreach (var (_, dst, rel, size) in files)
        {
            ct.ThrowIfCancellationRequested();
            var len = new FileInfo(dst).Length;
            if (len != size)
                throw new WriteException($"{rel} has the wrong size on the card ({len:N0} instead of {size:N0} bytes).");
            var h = HashFromDevice(dst, b => progress?.Report(
                new WriteProgress(WritePhase.Verifying, n, files.Count, done + b, total)), ct);
            if (!h.AsSpan().SequenceEqual(hashes[rel]))
                throw new WriteException($"{rel} reads back differently from what was written. The card may be faulty.");
            done += size;
            n++;
        }

        // 3. make it live
        progress?.Report(new WriteProgress(WritePhase.Finishing, files.Count, files.Count, total, total));
        Directory.Move(partial, final);
        Directory.SetLastWriteTimeUtc(final, Directory.GetLastWriteTimeUtc(source));
        if (cardRoot is not null)
        {
            foreach (var name in extras)
            {
                var from = Path.Combine(rootPartial, name);
                var to = Path.Combine(root, name);
                if (Directory.Exists(from))
                {
                    Directory.Move(from, to);
                    Directory.SetLastWriteTimeUtc(to, Directory.GetLastWriteTimeUtc(Path.Combine(cardRoot, name)));
                }
                else
                    File.Move(from, to);
            }
            Directory.Delete(rootPartial);
        }
        string? labelWarning = null;
        if (label is not null && !string.Equals(new DriveInfo(root).VolumeLabel, label, StringComparison.Ordinal))
        {
            if (!SetVolumeLabel(root, label.Length == 0 ? null : label))
                labelWarning = $"The card's name couldn't be set to match the factory card (error {Marshal.GetLastPInvokeError()}). " +
                               "This is unlikely to matter.";
        }
        return new WriteResult(final, files.Count, total, target.VolumeSerial, labelWarning);
    }

    /// <summary>Every file under folder and where it goes under dest. rel is its path on the card
    /// (navigation\db_aunz\... or config\create_cd), for messages and the hash table.</summary>
    static List<(string path, string dst, string rel, long size)> Files(string folder, string dest, string? prefix) =>
        Directory.EnumerateFiles(folder, "*", SearchOption.AllDirectories)
            .Select(f => (f, r: Path.GetRelativePath(folder, f)))
            .Select(x => (path: x.f, dst: Path.Combine(dest, x.r), rel: prefix is null ? x.r : Path.Combine(prefix, x.r),
                          size: new FileInfo(x.f).Length))
            .OrderBy(f => f.rel, StringComparer.OrdinalIgnoreCase).ToList();

    /// <summary>Every folder under folder (empty ones too) and where it goes under dest.</summary>
    static List<(string src, string dst)> Dirs(string folder, string dest) =>
        Directory.EnumerateDirectories(folder, "*", SearchOption.AllDirectories)
            .Select(d => (src: d, dst: Path.Combine(dest, Path.GetRelativePath(folder, d))))
            .OrderBy(d => d.dst.Length).ToList();

    static void DeleteItem(string path)
    {
        if (Directory.Exists(path))
            Directory.Delete(path, true);
        else if (File.Exists(path))
        {
            File.SetAttributes(path, FileAttributes.Normal);
            File.Delete(path);
        }
    }

    static bool SameVolume(DriveSnapshot a, DriveSnapshot b) =>
        string.Equals(a.Root, b.Root, StringComparison.OrdinalIgnoreCase) &&
        a.VolumeSerial == b.VolumeSerial &&
        (a.VolumeGuidPath is null || b.VolumeGuidPath is null ||
         string.Equals(a.VolumeGuidPath, b.VolumeGuidPath, StringComparison.OrdinalIgnoreCase));

    /// <summary>SHA-256 of a file read with FILE_FLAG_NO_BUFFERING, so the bytes come from the card,
    /// not from Windows' memory cache. Falls back to a normal read where that isn't supported.</summary>
    static unsafe byte[] HashFromDevice(string path, Action<long> onBytes, CancellationToken ct)
    {
        using var sha = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        SafeFileHandle h;
        try
        {
            h = File.OpenHandle(path, FileMode.Open, FileAccess.Read, FileShare.Read, NoBuffering);
        }
        catch (Exception e) when (e is IOException or ArgumentException or UnauthorizedAccessException)
        {
            h = File.OpenHandle(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        }
        void* mem = NativeMemory.AlignedAlloc(Block, 4096); // unbuffered reads need sector-aligned buffers
        try
        {
            using (h)
            {
                var span = new Span<byte>(mem, Block);
                long off = 0;
                int r;
                while ((r = RandomAccess.Read(h, span, off)) > 0)
                {
                    ct.ThrowIfCancellationRequested();
                    sha.AppendData(span[..r]);
                    off += r;
                    onBytes(off);
                    if (r < Block)
                        break;
                }
            }
        }
        finally
        {
            NativeMemory.AlignedFree(mem);
        }
        return sha.GetHashAndReset();
    }
}
