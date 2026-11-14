namespace OpenLion.Core.Drives;

/// <summary>What we know about one mounted volume at one moment. Pure data, so the
/// safety rules in <see cref="CardGuard"/> can be tested without real hardware.</summary>
public sealed record DriveSnapshot
{
    /// <summary>Drive root, e.g. "E:\".</summary>
    public required string Root { get; init; }

    /// <summary>macOS: the device the volume is mounted from, e.g. "/dev/disk4s1". Null on Windows.</summary>
    public string? DeviceNode { get; init; }

    /// <summary>Volume GUID path, e.g. "\\?\Volume{...}\". Stable while the card stays inserted,
    /// unlike the drive letter. Null if Windows didn't report one.</summary>
    public string? VolumeGuidPath { get; init; }

    public string Label { get; init; } = "";

    /// <summary>FAT volume serial number. Changes when a card is formatted.</summary>
    public uint VolumeSerial { get; init; }

    /// <summary>Windows physical disk number (\\.\PhysicalDriveN). Null if it couldn't be read.</summary>
    public int? PhysicalDiskNumber { get; init; }

    /// <summary>Hardware serial of the card/reader, if the reader reports one. Often empty for SD readers.</summary>
    public string? DiskSerial { get; init; }

    public long TotalBytes { get; init; }

    /// <summary>Free space on the volume.</summary>
    public long FreeBytes { get; init; }

    /// <summary>File system as Windows names it: "FAT32", "exFAT", "NTFS"; empty if unknown.</summary>
    public string FileSystem { get; init; } = "";

    /// <summary>Removable media (SD/USB) according to Windows.</summary>
    public bool IsRemovable { get; init; }

    /// <summary>The volume shares a physical disk with Windows itself.</summary>
    public bool IsSystemDisk { get; init; }

    /// <summary>Volume is write-protected (e.g. lock switch on).</summary>
    public bool IsReadOnly { get; init; }

    /// <summary>The volume contains the iQ navigation metadata files (navigation/cdrom.txt or cdrom.toc).</summary>
    public bool HasNavigationFolder { get; init; }

    /// <summary>Number of files and folders in the root, ignoring Windows' own system folders.</summary>
    public int RootEntryCount { get; init; }

    /// <summary>True for a Windows drive root ("E:\"); false for a macOS mount point ("/Volumes/CARD").</summary>
    public bool HasDriveLetter => Root.Length >= 2 && char.IsAsciiLetter(Root[0]) && Root[1] == ':';

    /// <summary>How the drive is named on screen: "E:" on Windows, the volume's name ("NO NAME") on macOS.</summary>
    public string DisplayRoot => HasDriveLetter ? Root.TrimEnd('\\') :
        Root.TrimEnd('/') is { Length: > 0 } r ? Path.GetFileName(r) : "/";

    /// <summary>What the user types to confirm writing: the drive letter ("E") on Windows, the volume's name on macOS.</summary>
    public string ConfirmName => HasDriveLetter ? Root[..1].ToUpperInvariant() : DisplayRoot;

    public override string ToString() => HasDriveLetter || Label.Length == 0 || Label != DisplayRoot
        ? $"{DisplayRoot}  {(Label.Length > 0 ? Label : "(no label)")}  ({TotalBytes / 1_000_000_000.0:0.0} GB)"
        : $"{DisplayRoot}  ({TotalBytes / 1_000_000_000.0:0.0} GB)";
}
