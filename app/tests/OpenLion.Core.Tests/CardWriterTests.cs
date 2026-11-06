using OpenLion.Core.Cards;
using OpenLion.Core.Drives;

namespace OpenLion.Core.Tests;

public class CardWriterTests
{
    static readonly DateTime Stamp = new(2011, 6, 16, 11, 43, 52, DateTimeKind.Utc);

    /// <summary>A built navigation/ folder and an empty "card" folder standing in for the drive root.</summary>
    static (TempDir t, string src, DriveSnapshot card) Setup()
    {
        var t = new TempDir();
        t.File("build/navigation/cdrom.txt", "Volume: CD_TEST", Stamp.ToLocalTime());
        t.File("build/navigation/db_aunz/db/auz.mp0", new string('m', 2_500_000), Stamp.AddDays(-1).ToLocalTime());
        t.File("build/navigation/db_aunz/db/empty.bin", "");
        var empty = Directory.CreateDirectory(t.Sub("build", "navigation", "db_ag", "telemat")).FullName;
        Directory.SetLastWriteTimeUtc(empty, Stamp);
        Directory.CreateDirectory(t.Sub("card"));
        var card = new DriveSnapshot
        {
            Root = t.Sub("card") + Path.DirectorySeparatorChar, VolumeSerial = 0xB1A40001, FileSystem = "FAT32",
            IsRemovable = true, TotalBytes = 4_000_000_000,
        };
        return (t, t.Sub("build", "navigation"), card);
    }

    static Func<GuardResult> Allow(DriveSnapshot d) => () => new GuardResult(d, []);

    [Fact]
    public void WritesVerifiesAndRenames()
    {
        var (t, src, card) = Setup();
        using (t)
        {
            var r = CardWriter.Write(src, card, Allow(card), label: null);
            var nav = t.Sub("card", "navigation");
            Assert.Equal(nav, r.NavigationPath);
            Assert.Equal(3, r.Files);
            Assert.Equal(2_500_000 + 15, r.Bytes);
            Assert.False(Directory.Exists(t.Sub("card", "navigation.partial")));
            foreach (var f in Directory.EnumerateFiles(src, "*", SearchOption.AllDirectories))
            {
                var dst = Path.Combine(nav, Path.GetRelativePath(src, f));
                Assert.Equal(File.ReadAllBytes(f), File.ReadAllBytes(dst));
                Assert.Equal(File.GetLastWriteTimeUtc(f), File.GetLastWriteTimeUtc(dst));
            }
            Assert.True(Directory.Exists(Path.Combine(nav, "db_ag", "telemat")));
            Assert.Equal(Stamp, Directory.GetLastWriteTimeUtc(Path.Combine(nav, "db_ag", "telemat")));
        }
    }

    [Fact]
    public void RefusedCardIsNotTouched()
    {
        var (t, src, card) = Setup();
        using (t)
        {
            var refused = () => new GuardResult(card, [new GuardFinding(GuardProblem.IsTheFactoryCard, "factory card")]);
            var e = Assert.Throws<WriteException>(() => CardWriter.Write(src, card, refused, null));
            Assert.Contains("factory card", e.Message);
            Assert.Empty(Directory.EnumerateFileSystemEntries(t.Sub("card")));
        }
    }

    [Fact]
    public void SwappedCardIsNotTouched()
    {
        var (t, src, card) = Setup();
        using (t)
        {
            var other = card with { VolumeSerial = 0x12345678 };
            Assert.Throws<WriteException>(() => CardWriter.Write(src, card, Allow(other), null));
            Assert.Empty(Directory.EnumerateFileSystemEntries(t.Sub("card")));
        }
    }

    [Fact]
    public void ReplacesOldOpenLionMaps()
    {
        var (t, src, card) = Setup();
        using (t)
        {
            t.File("card/navigation/old.bin", "old maps");
            t.File("card/navigation.partial/half.bin", "from a stopped write");
            CardWriter.Write(src, card, Allow(card), null);
            Assert.False(File.Exists(t.Sub("card", "navigation", "old.bin")));
            Assert.False(Directory.Exists(t.Sub("card", "navigation.partial")));
            Assert.True(File.Exists(t.Sub("card", "navigation", "cdrom.txt")));
        }
    }

    [Fact]
    public void WritesTheFactoryCardsOtherRootItems()
    {
        var (t, src, card) = Setup();
        using (t)
        {
            t.File("copy/card_root/Config/create_cd", "mkdir navigation", Stamp.ToLocalTime());
            Directory.CreateDirectory(t.Sub("copy", "card_root", "Config", "empty"));
            t.File("copy/card_root/SW version.txt", "SW 1.2");
            var r = CardWriter.Write(src, card, Allow(card), null, cardRoot: t.Sub("copy", "card_root"));

            Assert.Equal(5, r.Files);
            Assert.Equal("mkdir navigation", File.ReadAllText(t.Sub("card", "Config", "create_cd")));
            Assert.Equal(Stamp, File.GetLastWriteTimeUtc(t.Sub("card", "Config", "create_cd")));
            Assert.True(Directory.Exists(t.Sub("card", "Config", "empty")));
            Assert.Equal("SW 1.2", File.ReadAllText(t.Sub("card", "SW version.txt")));
            Assert.True(File.Exists(t.Sub("card", "navigation", "cdrom.txt")));
            Assert.False(Directory.Exists(t.Sub("card", "openlion.partial")));
            Assert.Equal(["Config", "navigation", "SW version.txt"],
                Directory.EnumerateFileSystemEntries(t.Sub("card")).Select(Path.GetFileName).Order(StringComparer.OrdinalIgnoreCase));
        }
    }

    [Fact]
    public void RewriteReplacesTheOldRootItems()
    {
        var (t, src, card) = Setup();
        using (t)
        {
            t.File("copy/card_root/Config/create_cd", "new");
            t.File("card/Config/create_cd", "old");
            t.File("card/Config/stale.txt", "old");
            t.File("card/openlion.partial/half.bin", "from a stopped write");
            CardWriter.Write(src, card, Allow(card), null, cardRoot: t.Sub("copy", "card_root"));
            Assert.Equal("new", File.ReadAllText(t.Sub("card", "Config", "create_cd")));
            Assert.False(File.Exists(t.Sub("card", "Config", "stale.txt")));
            Assert.False(Directory.Exists(t.Sub("card", "openlion.partial")));
        }
    }

    [Fact]
    public void MissingCardRootIsReported()
    {
        var (t, src, card) = Setup();
        using (t)
        {
            Assert.Throws<WriteException>(() => CardWriter.Write(src, card, Allow(card), null, cardRoot: t.Sub("nope")));
            Assert.Empty(Directory.EnumerateFileSystemEntries(t.Sub("card")));
        }
    }

    [Fact]
    public void CancelledWriteNeverLooksFinished()
    {
        var (t, src, card) = Setup();
        using (t)
        {
            using var cts = new CancellationTokenSource();
            var p = new SyncProgress<WriteProgress>(w => { if (w.BytesDone > 1_000_000) cts.Cancel(); });
            Assert.ThrowsAny<OperationCanceledException>(() => CardWriter.Write(src, card, Allow(card), null, p, cts.Token));
            Assert.False(Directory.Exists(t.Sub("card", "navigation")));
        }
    }

    [Fact]
    public void MissingBuildIsReported()
    {
        var (t, _, card) = Setup();
        using (t)
            Assert.Throws<WriteException>(() => CardWriter.Write(t.Sub("nope"), card, Allow(card), null));
    }
}
