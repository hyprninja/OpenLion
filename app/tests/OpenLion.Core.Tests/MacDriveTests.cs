#if !WINDOWS
using OpenLion.Core.Cards;
using OpenLion.Core.Drives;

namespace OpenLion.Core.Tests;

/// <summary>The macOS build's drive code, fed with diskutil output shaped like a real Mac's.</summary>
public class MacDriveTests
{
    static string Plist(params (string key, string value)[] items) =>
        """<?xml version="1.0" encoding="UTF-8"?><!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd"><plist version="1.0"><dict>""" +
        string.Concat(items.Select(i => $"<key>{i.key}</key>{i.value}")) + "</dict></plist>";

    static string S(string v) => $"<string>{v}</string>";
    static string I(long v) => $"<integer>{v}</integer>";
    static string B(bool v) => v ? "<true/>" : "<false/>";

    /// <summary>A card in a MacBook's built-in SD slot: the reader is internal, the media removable.</summary>
    static Dictionary<string, object> SdCard(string name = "NO NAME", string fs = "MS-DOS FAT32", bool writable = true,
        string uuid = "5C17A956-003A-3FF2-8E7F-A14B1E1059B3") => DiskUtil.ParsePlist(Plist(
        ("BusProtocol", S("Secure Digital")), ("DeviceIdentifier", S("disk4s1")), ("DeviceNode", S("/dev/disk4s1")),
        ("FilesystemName", S(fs)), ("FilesystemType", S("msdos")), ("FreeSpace", I(1_990_000_000)),
        ("Internal", B(true)), ("MountPoint", S($"/Volumes/{name}")), ("ParentWholeDisk", S("disk4")),
        ("Removable", B(true)), ("RemovableMedia", B(true)), ("TotalSize", I(1_998_000_000)),
        ("VolumeName", S(name)), ("VolumeUUID", S(uuid)), ("WritableMedia", B(writable)), ("WritableVolume", B(writable))));

    static Dictionary<string, object> Startup() => DiskUtil.ParsePlist(Plist(
        ("APFSPhysicalStores", "<array><dict><key>APFSPhysicalStore</key><string>disk0s2</string></dict></array>"),
        ("BusProtocol", S("Apple Fabric")), ("DeviceIdentifier", S("disk3s1s1")), ("DeviceNode", S("/dev/disk3s1s1")),
        ("FilesystemName", S("APFS")), ("FilesystemType", S("apfs")), ("FreeSpace", I(0)), ("APFSContainerFree", I(800_000_000_000)),
        ("Internal", B(true)), ("MountPoint", S("/")), ("ParentWholeDisk", S("disk3")), ("RemovableMedia", B(false)),
        ("TotalSize", I(994_000_000_000)), ("VolumeName", S("Macintosh HD")), ("VolumeUUID", S("DFBE1A1F-7A85-49C0-A48F-C73AEF9F7491")),
        ("WritableVolume", B(false))));

    static readonly HashSet<int> System = DriveService.SystemWholeDisks(Startup());

    [Fact]
    public void StartupDiskAndItsPhysicalStoreAreSystemDisks() =>
        Assert.Equal(new HashSet<int> { 3, 0 }, System);

    [Fact]
    public void ReadsAnSdCard()
    {
        var d = DriveService.FromInfo(SdCard("OPENLION"), "/Volumes/OPENLION", System, 0, false);
        Assert.Equal("/Volumes/OPENLION", d.Root);
        Assert.Equal("/dev/disk4s1", d.DeviceNode);
        Assert.Equal("FAT32", d.FileSystem);
        Assert.Equal(0x5C17A956u, d.VolumeSerial);
        Assert.Equal(4, d.PhysicalDiskNumber);
        Assert.True(d.IsRemovable);
        Assert.False(d.IsSystemDisk);
        Assert.False(d.IsReadOnly);
        Assert.Equal("OPENLION", d.DisplayRoot);
        Assert.Equal("OPENLION", d.ConfirmName);
        Assert.Equal("OPENLION  (2.0 GB)", d.ToString());
    }

    [Fact]
    public void LockedCardIsReadOnlyAndUnlabelledFatHasNoLabel()
    {
        var d = DriveService.FromInfo(SdCard(writable: false), "/Volumes/NO NAME", System, 3, true);
        Assert.True(d.IsReadOnly);
        Assert.Equal("", d.Label);
        Assert.Equal("NO NAME", d.DisplayRoot);
    }

    [Fact]
    public void DiskImagesAreNotCards() // macOS reports them as removable
    {
        var info = SdCard();
        info["BusProtocol"] = "Disk Image";
        Assert.False(DriveService.FromInfo(info, "/Volumes/NO NAME", System, 0, false).IsRemovable);
    }

    [Fact]
    public void StartupVolumeIsRefused()
    {
        var sys = DriveService.FromInfo(Startup(), "/", System, 10, false);
        Assert.True(sys.IsSystemDisk);
        Assert.False(sys.IsRemovable);
        Assert.Equal(800_000_000_000, sys.FreeBytes);
        var r = CardGuard.CheckTarget(sys, [sys], null, 0);
        Assert.Contains(r.Findings, f => f.Problem == GuardProblem.SystemDisk);
        Assert.Contains(r.Findings, f => f.Message == "This is the disk macOS runs from.");
    }

    [Fact]
    public void BlankCardIsAllowedOnlyOnceTheFactoryCardIsOut()
    {
        var blank = DriveService.FromInfo(SdCard(), "/Volumes/NO NAME", System, 0, false);
        var factoryInfo = SdCard("CD_7597", writable: false, uuid: "11111111-2222-3333-4444-555555555555");
        factoryInfo["DeviceNode"] = "/dev/disk5s1";
        factoryInfo["ParentWholeDisk"] = "disk5";
        var factory = DriveService.FromInfo(factoryInfo, "/Volumes/CD_7597", System, 2, true);
        var startup = DriveService.FromInfo(Startup(), "/", System, 10, false);
        var record = FactoryCardRecord.FromSnapshot(factory, "CD_7597", copyVerified: true);

        Assert.True(CardGuard.CheckTarget(blank, [startup, blank], record, 1_000_000_000).Allowed);
        var both = CardGuard.CheckTarget(blank, [startup, blank, factory], record, 1_000_000_000);
        Assert.Contains(both.Findings, f => f.Problem == GuardProblem.FactoryCardStillInserted);
        Assert.Contains(CardGuard.CheckTarget(factory, [startup, factory], record, 0).Findings,
            f => f.Problem == GuardProblem.IsTheFactoryCard);
    }

    [Fact]
    public void Fat16CardGetsTheExactFormatCommand()
    {
        var d = DriveService.FromInfo(SdCard(fs: "MS-DOS FAT16"), "/Volumes/NO NAME", System, 0, false);
        Assert.Equal("FAT16", d.FileSystem);
        var msg = Assert.Single(CardGuard.CheckTarget(d, [d], null, 0).Findings, f => f.Problem == GuardProblem.NotFat32).Message;
        Assert.Contains("diskutil eraseDisk FAT32 OPENLION MBRFormat /dev/disk4", msg);
    }

    [Theory]
    [InlineData("exfat", "ExFAT", "exFAT")]
    [InlineData("apfs", "APFS", "APFS")]
    [InlineData("msdos", "MS-DOS FAT32", "FAT32")]
    [InlineData("msdos", "MS-DOS FAT12", "FAT12")]
    public void FileSystemNamesMatchWindows(string type, string name, string expected) =>
        Assert.Equal(expected, DriveService.FileSystemName(type, name));

    [Fact]
    public void DiskNumbers()
    {
        Assert.Equal(4, DriveService.DiskNumber("disk4s1"));
        Assert.Equal(12, DriveService.DiskNumber("disk12"));
        Assert.Null(DriveService.DiskNumber("/dev/disk4"));
        Assert.Null(DriveService.DiskNumber(null));
        Assert.Equal(0u, DriveService.SerialFromUuid(null));
    }

    [Fact]
    public void WindowsRootsStillShowTheirLetter()
    {
        var d = new DriveSnapshot { Root = @"E:\", Label = "CD_7597", TotalBytes = 2_000_000_000 };
        Assert.Equal("E:", d.DisplayRoot);
        Assert.Equal("E", d.ConfirmName);
        Assert.Equal("E:  CD_7597  (2.0 GB)", d.ToString());
    }

    [Fact]
    public void CopyPathsAreComparedByMountPoint()
    {
        string[] mounts = ["/", "/Volumes/CARD", "/Volumes/CARD 1"];
        Assert.Equal("/Volumes/CARD", CardCopier.MountPointOf("/Volumes/CARD/navigation", mounts));
        Assert.Equal("/Volumes/CARD 1", CardCopier.MountPointOf("/Volumes/CARD 1/x", mounts));
        Assert.Equal("/", CardCopier.MountPointOf("/Users/me/Library/Application Support/OpenLion", mounts));
        Assert.True(CardCopier.IsInside("/Volumes/CARD/navigation/copy", "/Volumes/CARD"));
        Assert.False(CardCopier.IsInside("/Volumes/CARD 1/x", "/Volumes/CARD"));
    }

    [Fact]
    public void RealDriveListHasTheStartupDiskRefused() // this Mac's own drives, read-only
    {
        var drives = DriveService.GetDrives();
        var sys = Assert.Single(drives, d => d.Root == "/");
        Assert.True(sys.IsSystemDisk);
        Assert.NotNull(sys.DeviceNode);
        Assert.False(CardGuard.CheckTarget(sys, drives, null, 0).Allowed);
    }
}
#endif
