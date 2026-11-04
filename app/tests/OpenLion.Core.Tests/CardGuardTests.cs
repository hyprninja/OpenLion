using OpenLion.Core.Drives;

namespace OpenLion.Core.Tests;

public class CardGuardTests
{
    const long Need = 1_000_000_000;

    static DriveSnapshot Blank(string root = @"F:\", uint serial = 0xB1A4_0001, int disk = 3) => new()
    {
        Root = root,
        VolumeGuidPath = $@"\\?\Volume{{{serial:x8}}}\",
        Label = "",
        VolumeSerial = serial,
        PhysicalDiskNumber = disk,
        TotalBytes = 4_000_000_000,
        IsRemovable = true,
    };

    static DriveSnapshot SystemC() => new()
    {
        Root = @"C:\", VolumeGuidPath = @"\\?\Volume{c}\", Label = "OS", VolumeSerial = 0xC0C0,
        PhysicalDiskNumber = 0, TotalBytes = 500_000_000_000, IsSystemDisk = true, RootEntryCount = 20,
    };

    static readonly FactoryCardRecord Factory = new()
    {
        Label = "CD_7606", VolumeSerial = 0xFAC7_0001, TotalBytes = 4_000_000_000, CopyVerified = true,
    };

    static DriveSnapshot FactoryDrive(string root = @"E:\", int disk = 2) => Blank(root, Factory.VolumeSerial, disk) with
    {
        Label = "CD_7606", HasNavigationFolder = true, RootEntryCount = 1,
    };

    static GuardResult Check(DriveSnapshot target, IEnumerable<DriveSnapshot>? others = null,
        FactoryCardRecord? factory = null, GuardOptions? opt = null) =>
        CardGuard.CheckTarget(target, [target, .. others ?? [SystemC()]], factory ?? Factory, Need, opt);

    static GuardProblem[] Problems(GuardResult r) => r.Findings.Select(f => f.Problem).ToArray();

    [Fact]
    public void BlankCardAloneIsAllowed()
    {
        var r = Check(Blank());
        Assert.True(r.Allowed, string.Join("; ", r.Findings.Select(f => f.Message)));
    }

    [Fact]
    public void FactoryCardIsRefused()
    {
        var r = Check(FactoryDrive());
        Assert.False(r.Allowed);
        Assert.Contains(GuardProblem.IsTheFactoryCard, Problems(r));
    }

    [Fact]
    public void FactoryCardRefusedEvenWhenUserAllowsNonEmptyAndItIsMarkedWrittenByUs()
    {
        // The volume serial match wins over every user option.
        var opt = new GuardOptions { AllowNonEmpty = true, WrittenByOpenLion = new HashSet<uint> { Factory.VolumeSerial } };
        Assert.Contains(GuardProblem.IsTheFactoryCard, Problems(Check(FactoryDrive(), opt: opt)));
    }

    [Fact]
    public void FactoryCardWithNavigationFolderDeletedIsStillRefusedBySerial()
    {
        var wiped = FactoryDrive() with { HasNavigationFolder = false, RootEntryCount = 0 };
        Assert.Contains(GuardProblem.IsTheFactoryCard, Problems(Check(wiped)));
    }

    [Fact]
    public void UnknownCardWithNavigationFolderIsRefused()
    {
        // e.g. a second factory card, or a card from another car
        var other = Blank() with { HasNavigationFolder = true, RootEntryCount = 1 };
        var r = Check(other, opt: new GuardOptions { AllowNonEmpty = true });
        Assert.Contains(GuardProblem.LooksLikeFactoryCard, Problems(r));
    }

    [Fact]
    public void CardWrittenByOpenLionCanBeRewritten()
    {
        var ours = Blank() with { HasNavigationFolder = true, RootEntryCount = 1, Label = "CD_7606" };
        var r = Check(ours, opt: new GuardOptions { WrittenByOpenLion = new HashSet<uint> { ours.VolumeSerial } });
        Assert.True(r.Allowed, string.Join("; ", r.Findings.Select(f => f.Message)));
    }

    [Fact]
    public void RefusedWhileFactoryCardIsStillInserted()
    {
        var r = Check(Blank(), [SystemC(), FactoryDrive()]);
        Assert.Contains(GuardProblem.FactoryCardStillInserted, Problems(r));
    }

    [Fact]
    public void RefusedWhileAnyNavigationCardIsInsertedEvenIfNotRemovable()
    {
        // A factory card in a reader that reports itself as a fixed disk
        var fixedFactory = FactoryDrive() with { IsRemovable = false, VolumeSerial = 0x1234 };
        Assert.Contains(GuardProblem.FactoryCardStillInserted, Problems(Check(Blank(), [SystemC(), fixedFactory])));
    }

    [Fact]
    public void RefusedWhenTwoCardsAreInserted()
    {
        var r = Check(Blank(), [SystemC(), Blank(@"G:\", 0x2222, 4)]);
        Assert.Contains(GuardProblem.OtherCardsInserted, Problems(r));
    }

    [Fact]
    public void SameVolumeListedTwiceIsNotTreatedAsAnotherCard()
    {
        var b = Blank();
        var r = CardGuard.CheckTarget(b, [b, b with { }, SystemC()], Factory, Need);
        Assert.True(r.Allowed);
    }

    [Fact]
    public void RefusedWhenDiskHasAnotherVolume()
    {
        var second = Blank(@"G:\", 0x3333) with { IsRemovable = false };
        Assert.Contains(GuardProblem.SharesDiskWithOtherVolume, Problems(Check(Blank(), [SystemC(), second])));
    }

    [Fact]
    public void RefusedWithoutVerifiedCopy()
    {
        Assert.Contains(GuardProblem.NoVerifiedFactoryCopy,
            Problems(CardGuard.CheckTarget(Blank(), [Blank(), SystemC()], null, Need)));
        Assert.Contains(GuardProblem.NoVerifiedFactoryCopy,
            Problems(Check(Blank(), factory: Factory with { CopyVerified = false })));
    }

    [Fact]
    public void SystemDiskIsRefused()
    {
        var r = Check(SystemC(), []);
        Assert.Contains(GuardProblem.SystemDisk, Problems(r));
        Assert.Contains(GuardProblem.NotRemovable, Problems(r));
        Assert.Contains(GuardProblem.TooLarge, Problems(r));
    }

    [Fact]
    public void FixedUsbDiskIsRefused()
    {
        var hdd = Blank() with { IsRemovable = false, TotalBytes = 2_000_000_000_000 };
        var p = Problems(Check(hdd));
        Assert.Contains(GuardProblem.NotRemovable, p);
        Assert.Contains(GuardProblem.TooLarge, p);
    }

    [Fact]
    public void UnknownDiskNumberIsRefused() =>
        Assert.Contains(GuardProblem.UnknownPhysicalDisk, Problems(Check(Blank() with { PhysicalDiskNumber = null })));

    [Fact]
    public void WriteProtectedIsRefused() =>
        Assert.Contains(GuardProblem.WriteProtected, Problems(Check(Blank() with { IsReadOnly = true })));

    [Fact]
    public void TooSmallIsRefused() =>
        Assert.Contains(GuardProblem.TooSmall, Problems(Check(Blank() with { TotalBytes = 512_000_000 })));

    [Theory]
    [InlineData(31_900_000_000L, true)]
    [InlineData(63_800_000_000L, false)]
    public void SizeLimit(long size, bool allowed) =>
        Assert.Equal(allowed, Check(Blank() with { TotalBytes = size }).Allowed);

    [Fact]
    public void NonEmptyCardNeedsConfirmation()
    {
        var used = Blank() with { RootEntryCount = 5 };
        Assert.Contains(GuardProblem.NotEmpty, Problems(Check(used)));
        Assert.True(Check(used, opt: new GuardOptions { AllowNonEmpty = true }).Allowed);
    }
}
