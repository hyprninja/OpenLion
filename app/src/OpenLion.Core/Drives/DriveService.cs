using System.Buffers.Binary;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
using static OpenLion.Core.Drives.NativeMethods;

namespace OpenLion.Core.Drives;

/// <summary>Reads the current set of drives into <see cref="DriveSnapshot"/>s. Read-only.</summary>
public static class DriveService
{
    static readonly HashSet<string> IgnoredRootEntries = new(StringComparer.OrdinalIgnoreCase)
    {
        "System Volume Information", "$RECYCLE.BIN",
    };

    /// <summary>All ready local drives (fixed and removable). Drives that fail to read are skipped.</summary>
    public static List<DriveSnapshot> GetDrives()
    {
        int? systemDisk = DiskNumberOf(Path.GetPathRoot(Environment.SystemDirectory)!);
        var list = new List<DriveSnapshot>();
        foreach (var di in DriveInfo.GetDrives())
        {
            if (di.DriveType is not (DriveType.Removable or DriveType.Fixed))
                continue;
            try
            {
                if (di.IsReady)
                    list.Add(Read(di, systemDisk));
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                // card pulled mid-read, or a locked volume: leave it out of this snapshot
            }
        }
        return list;
    }

    static DriveSnapshot Read(DriveInfo di, int? systemDisk)
    {
        var root = di.RootDirectory.FullName;
        var (serial, flags) = VolumeInfo(root);
        int? disk = DiskNumberOf(root);
        var (busType, diskSerial) = DeviceInfo(root);
        bool removable = di.DriveType == DriveType.Removable || busType is BusTypeSd or BusTypeMmc;
        bool readOnly = (flags & FILE_READ_ONLY_VOLUME) != 0 || !IsWritable(root);

        return new DriveSnapshot
        {
            Root = root,
            VolumeGuidPath = VolumeGuid(root),
            Label = di.VolumeLabel,
            VolumeSerial = serial,
            PhysicalDiskNumber = disk,
            DiskSerial = diskSerial,
            TotalBytes = di.TotalSize,
            FreeBytes = di.AvailableFreeSpace,
            FileSystem = di.DriveFormat,
            IsRemovable = removable,
            IsSystemDisk = disk is not null && disk == systemDisk,
            IsReadOnly = readOnly,
            HasNavigationFolder = NavigationCard.HasMarkers(root),
            RootEntryCount = di.RootDirectory.EnumerateFileSystemInfos()
                .Count(e => !IgnoredRootEntries.Contains(e.Name)),
        };
    }

    /// <summary>Volume GUID path currently mounted at a drive root, or null.</summary>
    public static string? VolumeGuidOf(string root) => VolumeGuid(root);

    static SafeFileHandle OpenQueryOnly(string root) =>
        CreateFile(@"\\.\" + root.TrimEnd('\\'), 0, FILE_SHARE_READ | FILE_SHARE_WRITE, 0, OPEN_EXISTING, 0, 0);

    static unsafe string? VolumeGuid(string root)
    {
        char* buf = stackalloc char[64];
        return GetVolumeNameForVolumeMountPoint(root, buf, 64) ? new string(buf) : null;
    }

    static unsafe (uint serial, uint flags) VolumeInfo(string root)
    {
        if (!GetVolumeInformation(root, null, 0, out uint serial, out _, out uint flags, null, 0))
            throw new IOException($"GetVolumeInformation failed for {root} ({Marshal.GetLastPInvokeError()})");
        return (serial, flags);
    }

    /// <summary>Physical disk number, or null if the volume spans several disks or can't be queried.</summary>
    static unsafe int? DiskNumberOf(string root)
    {
        using var h = OpenQueryOnly(root);
        if (h.IsInvalid)
            return null;
        byte* buf = stackalloc byte[64];
        if (!DeviceIoControl(h, IOCTL_VOLUME_GET_VOLUME_DISK_EXTENTS, null, 0, buf, 64, out _, 0))
            return null;
        // VOLUME_DISK_EXTENTS: NumberOfDiskExtents (u32), pad, then DISK_EXTENT { DiskNumber (u32), ... }
        var span = new ReadOnlySpan<byte>(buf, 64);
        return BinaryPrimitives.ReadUInt32LittleEndian(span) == 1
            ? (int)BinaryPrimitives.ReadUInt32LittleEndian(span[8..])
            : null;
    }

    /// <summary>Bus type and hardware serial from STORAGE_DEVICE_DESCRIPTOR.</summary>
    static unsafe (int busType, string? serial) DeviceInfo(string root)
    {
        using var h = OpenQueryOnly(root);
        if (h.IsInvalid)
            return (0, null);
        int* query = stackalloc int[3]; // STORAGE_PROPERTY_QUERY: StorageDeviceProperty, PropertyStandardQuery
        query[0] = 0; query[1] = 0; query[2] = 0;
        const int len = 1024;
        byte* buf = stackalloc byte[len];
        if (!DeviceIoControl(h, IOCTL_STORAGE_QUERY_PROPERTY, query, 12, buf, len, out int got, 0) || got < 32)
            return (0, null);
        var s = new ReadOnlySpan<byte>(buf, got);
        int serialOff = (int)BinaryPrimitives.ReadUInt32LittleEndian(s[24..]);
        int busType = BinaryPrimitives.ReadInt32LittleEndian(s[28..]);
        string? serial = null;
        if (serialOff > 0 && serialOff < got)
        {
            int end = s[serialOff..].IndexOf((byte)0);
            serial = Encoding.ASCII.GetString(end < 0 ? s[serialOff..] : s.Slice(serialOff, end)).Trim();
            if (serial.Length == 0) serial = null;
        }
        return (busType, serial);
    }

    /// <summary>False if the disk reports write protection (e.g. SD lock switch).</summary>
    static unsafe bool IsWritable(string root)
    {
        using var h = OpenQueryOnly(root);
        if (h.IsInvalid)
            return true; // unknown: the volume flag check still applies
        return DeviceIoControl(h, IOCTL_DISK_IS_WRITABLE, null, 0, null, 0, out _, 0) ||
               Marshal.GetLastPInvokeError() != ERROR_WRITE_PROTECT;
    }
}
