using OpenLion.Core.Cards;

namespace OpenLion.Core.Tests;

public class CardCopierTests
{
    static readonly DateTime Factory = new(2011, 6, 16, 11, 43, 52);

    static TempDir FakeCard()
    {
        var t = new TempDir();
        t.File("card/navigation/cdrom.txt", "Volume: CD_TEST", Factory);
        t.File("card/navigation/db_aunz/db/auz.mp0", new string('x', 3_000_000), Factory.AddDays(-1));
        t.File("card/navigation/db_aunz/db/empty.bin", "");
        t.File("card/navigation/voices/en/a.wav", "voice");
        Directory.CreateDirectory(t.Sub("card", "navigation", "db_ag", "telemat")); // empty folder, like the real card
        Directory.SetLastWriteTime(t.Sub("card", "navigation", "db_ag", "telemat"), Factory);
        return t;
    }

    static Dictionary<string, (long, DateTime)> Snapshot(string dir) =>
        Directory.EnumerateFiles(dir, "*", SearchOption.AllDirectories)
            .ToDictionary(f => f, f => (new FileInfo(f).Length, File.GetLastWriteTimeUtc(f)));

    [Fact]
    public void CopiesEveryFileAndWritesVerifiedManifest()
    {
        using var t = FakeCard();
        var before = Snapshot(t.Sub("card"));
        var r = CardCopier.Copy(t.Sub("card"), t.Sub("copy"), null, default, allowSameDrive: true);

        Assert.Equal(4, r.Manifest.Count);
        Assert.Equal(before.Values.Sum(v => v.Item1), r.TotalBytes);
        foreach (var e in r.Manifest)
        {
            var copied = Path.Combine(r.CopyRoot, e.Path.Replace('/', '\\'));
            Assert.Equal(e.Sha256, CardCopier.Sha256File(copied));
            Assert.Equal(e.Sha256, CardCopier.Sha256File(Path.Combine(t.Sub("card"), e.Path.Replace('/', '\\'))));
        }
        Assert.Equal(Factory, File.GetLastWriteTime(Path.Combine(r.NavigationPath, "cdrom.txt")));
        Assert.Equal(Factory, Directory.GetLastWriteTime(Path.Combine(r.NavigationPath, "db_ag", "telemat")));
        Assert.Equal(r.Manifest.OrderBy(e => e.Path, StringComparer.Ordinal), FileManifest.Read(r.ManifestPath));
        Assert.False(Directory.Exists(t.Sub("copy") + ".partial"));
        Assert.Equal(before, Snapshot(t.Sub("card"))); // card untouched
    }

    [Fact]
    public void ReplacesAnOldCopy()
    {
        using var t = FakeCard();
        t.File("copy/navigation/stale.txt", "old");
        var r = CardCopier.Copy(t.Sub("card"), t.Sub("copy"), null, default, allowSameDrive: true);
        Assert.False(File.Exists(Path.Combine(r.NavigationPath, "stale.txt")));
    }

    [Fact]
    public void RefusesSameDriveInPublicApi()
    {
        using var t = FakeCard();
        Assert.Throws<CopyException>(() => CardCopier.Copy(t.Sub("card"), t.Sub("copy")));
    }

    [Fact]
    public void RefusesCopyInsideTheCard()
    {
        using var t = FakeCard();
        Assert.Throws<CopyException>(() =>
            CardCopier.Copy(t.Sub("card"), t.Sub("card", "navigation", "copy"), null, default, allowSameDrive: true));
    }

    [Fact]
    public void RefusesDriveWithoutNavigationFolder()
    {
        using var t = new TempDir();
        Directory.CreateDirectory(t.Sub("empty"));
        Assert.Throws<CopyException>(() => CardCopier.Copy(t.Sub("empty"), t.Sub("copy"), null, default, allowSameDrive: true));
    }

    [Fact]
    public void CancelLeavesNoFinishedCopy()
    {
        using var t = FakeCard();
        using var cts = new CancellationTokenSource();
        var p = new SyncProgress<CopyProgress>(_ => cts.Cancel());
        Assert.ThrowsAny<OperationCanceledException>(() =>
            CardCopier.Copy(t.Sub("card"), t.Sub("copy"), p, cts.Token, allowSameDrive: true));
        Assert.False(File.Exists(t.Sub("copy", "manifest.tsv")));
    }

    [Fact]
    public void ReportsProgressToTheEnd()
    {
        using var t = FakeCard();
        CopyProgress? last = null;
        CardCopier.Copy(t.Sub("card"), t.Sub("copy"), new SyncProgress<CopyProgress>(p => last = p), default, allowSameDrive: true);
        Assert.NotNull(last);
        Assert.Equal(last.BytesTotal, last.BytesDone);
        Assert.Equal(4, last.FilesDone);
    }
}

/// <summary>Progress&lt;T&gt; posts to a sync context; this one calls back immediately.</summary>
sealed class SyncProgress<T>(Action<T> a) : IProgress<T>
{
    public void Report(T value) => a(value);
}
