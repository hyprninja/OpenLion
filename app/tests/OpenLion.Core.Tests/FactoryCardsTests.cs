using OpenLion.Core.Drives;

namespace OpenLion.Core.Tests;

public class FactoryCardsTests
{
    static DriveSnapshot Card(string root, uint serial, bool nav = true) => new()
    {
        Root = root, VolumeSerial = serial, FileSystem = "FAT32", IsRemovable = true, TotalBytes = 4_000_000_000,
        HasNavigationFolder = nav,
    };

    [Fact]
    public void CardsOpenLionWroteAreNotOfferedAsFactoryCards()
    {
        var factory = Card(@"F:\", 0x1CF5423A);
        var ours = Card(@"E:\", 0x6038741A);
        var blank = Card(@"G:\", 0x11111111, nav: false);
        var (f, o) = FactoryCards.Split([factory, ours, blank], new HashSet<uint> { 0x6038741A });
        Assert.Equal([factory], f);
        Assert.Equal([ours], o);
        Assert.Equal("E: is a card OpenLion wrote, so it isn't offered as a factory card.", FactoryCards.OpenLionNote(o));
    }

    [Fact]
    public void NoteForNoneAndForSeveral()
    {
        Assert.Equal("", FactoryCards.OpenLionNote([]));
        Assert.Equal("E: and H: are cards OpenLion wrote, so they aren't offered as factory cards.",
            FactoryCards.OpenLionNote([Card(@"E:\", 1), Card(@"H:\", 2)]));
    }

    static FactoryCardRecord Record(bool verified = true) => new()
    {
        Label = "", VolumeSerial = 0x1CF5423A, TotalBytes = 4_000_000_000, CopyVerified = verified,
    };

    [Fact]
    public void SavedCopyIsUsableOnlyWhenVerifiedPresentAndFrom13()
    {
        using var t = new TempDir();
        t.File("copy/navigation/cdrom.txt", "Volume: CD_TEST");
        var state = new AppState { Factory = Record(), FactoryCopyPath = t.Sub("copy") };
        Assert.Null(state.UsableFactoryCopy());                       // no card_root/: made before 1.3

        Directory.CreateDirectory(t.Sub("copy", "card_root"));
        Assert.Equal(state.Factory, state.UsableFactoryCopy());

        Assert.Null(new AppState { Factory = Record(verified: false), FactoryCopyPath = t.Sub("copy") }.UsableFactoryCopy());
        Assert.Null(new AppState { Factory = Record(), FactoryCopyPath = t.Sub("gone") }.UsableFactoryCopy());
        Assert.Null(new AppState { Factory = null, FactoryCopyPath = t.Sub("copy") }.UsableFactoryCopy());
        Assert.Null(new AppState { Factory = Record(), FactoryCopyPath = null }.UsableFactoryCopy());
    }
}
