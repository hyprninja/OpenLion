using OpenLion.Core.Drives;

namespace OpenLion.Core.Tests;

public class DriveServiceTests
{
    [Fact]
    public void SystemDriveIsReportedAsSystemDiskAndRefused()
    {
        var drives = DriveService.GetDrives();
        var sysRoot = Path.GetPathRoot(Environment.SystemDirectory)!;
        var sys = Assert.Single(drives, d => string.Equals(d.Root, sysRoot, StringComparison.OrdinalIgnoreCase));
        Assert.True(sys.IsSystemDisk);
        Assert.NotNull(sys.PhysicalDiskNumber);
        Assert.NotNull(sys.VolumeGuidPath);
        Assert.NotEqual(0u, sys.VolumeSerial);
        Assert.False(CardGuard.CheckTarget(sys, drives, null, 0).Allowed);
    }
}
