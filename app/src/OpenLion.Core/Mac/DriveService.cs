namespace OpenLion.Core.Drives;

/// <summary>macOS: reads the current set of mounted volumes into <see cref="DriveSnapshot"/>s, from
/// diskutil. Read-only. The Windows build has its own DriveService (Drives/DriveService.cs).</summary>
public static class DriveService
{
    /// <summary>What Windows and macOS put on any drive they mount. Not counted as files on a card.</summary>
    static readonly HashSet<string> IgnoredRootEntries = new(StringComparer.OrdinalIgnoreCase)
    {
        "System Volume Information", "$RECYCLE.BIN",
        ".Spotlight-V100", ".fseventsd", ".Trashes", ".TemporaryItems", ".DS_Store", ".metadata_never_index",
        ".apdisk", ".VolumeIcon.icns", ".DocumentRevisions-V100", ".com.apple.timemachine.donotpresent",
    };

    /// <summary>True for the hidden files and folders an operating system puts on any drive it mounts.</summary>
    public static bool IsSystemItem(string name) =>
        IgnoredRootEntries.Contains(name) || name.StartsWith("._", StringComparison.Ordinal);

    /// <summary>The startup volume and every local volume under /Volumes. Network shares and
    /// volumes that fail to read are skipped.</summary>
    public static List<DriveSnapshot> GetDrives()
    {
        var mounts = DriveInfo.GetDrives()
            .Where(d => d.Name == "/" || d.Name.StartsWith("/Volumes/", StringComparison.Ordinal))
            .Where(d => d.DriveType is not (DriveType.Network or DriveType.NoRootDirectory))
            .Select(d => d.Name)
            .ToList();
        var infos = mounts.AsParallel().AsOrdered().WithDegreeOfParallelism(4)
            .Select(m => (mount: m, info: SafeInfo(m)))
            .ToList();
        var startup = infos.FirstOrDefault(i => i.mount == "/").info;
        var systemDisks = SystemWholeDisks(startup);

        var list = new List<DriveSnapshot>();
        foreach (var (mount, info) in infos)
        {
            if (info is null)
                continue;
            try
            {
                list.Add(FromInfo(info, mount, systemDisks, CountRootEntries(mount), NavigationCard.HasMarkers(mount)));
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                // card pulled mid-read: leave it out of this snapshot
            }
        }
        return list;
    }

    /// <summary>The device a volume is mounted from ("/dev/disk4s1"), or null.</summary>
    public static string? VolumeGuidOf(string root) => SafeInfo(root)?.Str("DeviceNode");

    static Dictionary<string, object>? SafeInfo(string mount)
    {
        try { return DiskUtil.Info(mount); }
        catch (Exception e) when (e is IOException or InvalidOperationException or System.ComponentModel.Win32Exception
                                       or System.Xml.XmlException or FormatException)
        {
            return null;
        }
    }

    static int CountRootEntries(string mount) =>
        new DirectoryInfo(mount).EnumerateFileSystemInfos().Count(e => !IsSystemItem(e.Name));

    /// <summary>Whole-disk numbers macOS runs from: the startup volume's disk (an APFS container,
    /// e.g. disk3) and the physical disks under it (disk0).</summary>
    internal static HashSet<int> SystemWholeDisks(IReadOnlyDictionary<string, object>? startup)
    {
        var set = new HashSet<int>();
        if (startup is null)
            return set;
        if (DiskNumber(startup.Str("ParentWholeDisk")) is { } n)
            set.Add(n);
        if (startup.TryGetValue("APFSPhysicalStores", out var v) && v is List<object> stores)
            foreach (var s in stores.OfType<Dictionary<string, object>>())
                if (DiskNumber(s.Str("APFSPhysicalStore")) is { } p)
                    set.Add(p);
        return set;
    }

    /// <summary>"disk4" or "disk4s1" → 4.</summary>
    internal static int? DiskNumber(string? id)
    {
        if (id is null || !id.StartsWith("disk", StringComparison.Ordinal))
            return null;
        var digits = new string(id[4..].TakeWhile(char.IsAsciiDigit).ToArray());
        return int.TryParse(digits, out var n) ? n : null;
    }

    /// <summary>Turns one volume's diskutil info into a snapshot. Pure, so it can be tested with saved output.</summary>
    internal static DriveSnapshot FromInfo(IReadOnlyDictionary<string, object> info, string mount,
        IReadOnlySet<int> systemDisks, int rootEntryCount, bool hasNavigation)
    {
        var bus = info.Str("BusProtocol") ?? "";
        // macOS calls a mounted disk image "removable"; it isn't a card.
        bool virtualDisk = bus == "Disk Image" || info.Str("VirtualOrPhysical") == "Virtual";
        bool removable = !virtualDisk && (info.Bool("RemovableMedia") || bus == "Secure Digital");
        bool readOnly = !info.Bool("WritableVolume", true) || !info.Bool("WritableMedia", true) || info.Bool("ReadOnlyMedia");
        int? disk = DiskNumber(info.Str("ParentWholeDisk")) ?? DiskNumber(info.Str("DeviceIdentifier"));
        var fs = FileSystemName(info.Str("FilesystemType"), info.Str("FilesystemName"));
        var label = info.Str("VolumeName") ?? "";
        if (fs.StartsWith("FAT", StringComparison.Ordinal) && label == "NO NAME")
            label = ""; // how macOS shows a FAT volume without a label
        long free = info.Long("FreeSpace") is > 0 and var f ? f : info.Long("APFSContainerFree");

        return new DriveSnapshot
        {
            Root = info.Str("MountPoint") is { Length: > 0 } mp ? mp : mount,
            VolumeGuidPath = info.Str("DeviceNode"),
            DeviceNode = info.Str("DeviceNode"),
            Label = label,
            VolumeSerial = SerialFromUuid(info.Str("VolumeUUID")),
            PhysicalDiskNumber = disk,
            DiskSerial = null,
            TotalBytes = info.Long("TotalSize") is > 0 and var t ? t : info.Long("Size"),
            FreeBytes = free,
            FileSystem = fs,
            IsRemovable = removable,
            IsSystemDisk = disk is not null && systemDisks.Contains(disk.Value),
            IsReadOnly = readOnly,
            HasNavigationFolder = hasNavigation,
            RootEntryCount = rootEntryCount,
        };
    }

    /// <summary>"MS-DOS FAT32" → "FAT32", the names Windows uses, so the same rules apply.</summary>
    internal static string FileSystemName(string? type, string? name)
    {
        name ??= "";
        if (name.StartsWith("MS-DOS FAT", StringComparison.OrdinalIgnoreCase))
            return name["MS-DOS ".Length..].ToUpperInvariant();
        return type?.ToLowerInvariant() switch
        {
            "exfat" => "exFAT",
            "ntfs" => "NTFS",
            "apfs" => "APFS",
            "hfs" => "HFS+",
            "msdos" => "FAT",
            null or "" => "",
            _ => name.Length > 0 ? name : type,
        };
    }

    /// <summary>A FAT volume's UUID on macOS is made from its FAT volume serial number, so like the serial
    /// it is the same each time the card is inserted and changes when the card is formatted. Its first
    /// 32 bits stand in for the serial. 0 when macOS reports none.</summary>
    internal static uint SerialFromUuid(string? uuid) =>
        uuid is { Length: >= 8 } && uint.TryParse(uuid[..8], System.Globalization.NumberStyles.HexNumber, null, out var s) ? s : 0;
}
