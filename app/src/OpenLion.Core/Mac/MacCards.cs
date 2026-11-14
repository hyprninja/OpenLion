namespace OpenLion.Core.Drives;

/// <summary>macOS only: the few things OpenLion asks macOS to do with a mounted card, by device
/// ("/dev/disk4s1"), which stays the same while the card is in the reader even when its mount point
/// changes. None of these write to the card's files.</summary>
public static class MacCards
{
    /// <summary>Unmounts the card and mounts it again, and returns where it is now mounted. Everything
    /// cached in memory for the card is dropped, so the next reads come from the card itself.</summary>
    public static DriveSnapshot Remount(DriveSnapshot card, bool readOnly = false)
    {
        var device = card.DeviceNode ?? throw new DiskUtilException($"macOS didn't report which device {card.DisplayRoot} is.");
        DiskUtil.Unmount(device);
        if (readOnly)
            DiskUtil.MountReadOnly(device);
        else
            DiskUtil.Mount(device);
        return Find(device, card.VolumeSerial)
               ?? throw new DiskUtilException($"{card.DisplayRoot} didn't come back after it was mounted again.");
    }

    /// <summary>The card mounted from this device now, if it is still the card with this serial.</summary>
    public static DriveSnapshot? Find(string device, uint volumeSerial) =>
        DriveService.GetDrives().FirstOrDefault(d => d.DeviceNode == device && d.VolumeSerial == volumeSerial);

    /// <summary>Ejects the card's whole disk, so it can be taken out safely.</summary>
    public static void Eject(DriveSnapshot card) =>
        DiskUtil.Eject(card.DeviceNode ?? throw new DiskUtilException($"macOS didn't report which device {card.DisplayRoot} is."));
}
