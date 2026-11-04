using OpenLion.Core.Cards;

namespace OpenLion.Core.Tests;

public class KnownCardsTests
{
    static readonly DateTime T = new(2011, 1, 1);
    static readonly string H1 = new('1', 64), H2 = new('2', 64), H3 = new('3', 64);

    [Fact]
    public void ExactMatchIsVerifiedAndDifferencesAreCounted()
    {
        using var t = new TempDir();
        var folder = t.Sub("known");
        Directory.CreateDirectory(folder);
        FileManifest.Write(Path.Combine(folder, "CD_TEST.tsv"),
            [new("navigation/a", 1, T, H1), new("navigation/b", 2, T, H2)]);

        var exact = KnownCards.Identify([new("navigation/a", 1, T, H1), new("navigation/b", 2, T, H2)], folder)!;
        Assert.Equal(new KnownCardMatch("CD_TEST", true, 0), exact);

        var other = KnownCards.Identify([new("navigation/a", 1, T, H1), new("navigation/b", 2, T, H3),
            new("navigation/c", 3, T, H3)], folder)!;
        Assert.False(other.Exact);
        Assert.Equal(2, other.Differences);
    }

    [Fact]
    public void NoKnownCardsFolderMeansNoMatch() =>
        Assert.Null(KnownCards.Identify([], Path.Combine(Path.GetTempPath(), "no-such-openlion-folder")));

    [Fact]
    public void ReadsTheRepoReferenceManifest()
    {
        // docs/reference/manifest_sha256.tsv ships with the app as KnownCards/CD_7606.tsv
        var tsv = Path.Combine(AppContext.BaseDirectory, "KnownCards", "CD_7606.tsv");
        var entries = FileManifest.Read(tsv);
        Assert.Equal(2225, entries.Count);
        Assert.Contains(entries, e => e.Path == "navigation/cdrom.txt" && e.Size == 720);
    }
}
