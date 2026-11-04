namespace OpenLion.Core.Drives;

/// <summary>What we know about one mounted volume at one moment. Pure data, so the
/// safety rules in <see cref="CardGuard"/> can be tested without real hardware.</summary>
public sealed record DriveSnapshot
{
    /// <summary>Drive root, e.g. "E:\".</summary>
    public required string Root { get; init; }

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

    public override string ToString() =>
        $"{Root.TrimEnd('\\')}  {(Label.Length > 0 ? Label : "(no label)")}  ({TotalBytes / 1_000_000_000.0:0.0} GB)";
}
