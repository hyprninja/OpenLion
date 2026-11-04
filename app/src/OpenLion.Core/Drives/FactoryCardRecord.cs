namespace OpenLion.Core.Drives;

/// <summary>Identity of the factory card the user copied, saved after the copy so the app
/// can recognise that card again later and refuse to write to it.</summary>
public sealed record FactoryCardRecord
{
    public required string Label { get; init; }
    public required uint VolumeSerial { get; init; }
    public string? DiskSerial { get; init; }
    public long TotalBytes { get; init; }

    /// <summary>"Volume:" line from navigation/cdrom.txt, e.g. "CD_7597 (= CD_7606 + CD_7335)".</summary>
    public string? CardVersion { get; init; }

    /// <summary>True once every file of the copy has been hash-verified against the card.</summary>
    public bool CopyVerified { get; init; }

    public static FactoryCardRecord FromSnapshot(DriveSnapshot d, string? cardVersion, bool copyVerified) => new()
    {
        Label = d.Label,
        VolumeSerial = d.VolumeSerial,
        DiskSerial = d.DiskSerial,
        TotalBytes = d.TotalBytes,
        CardVersion = cardVersion,
        CopyVerified = copyVerified,
    };

    /// <summary>Same card: matching FAT volume serial. The hardware serial is not used, because a
    /// USB reader usually reports its own serial, so two different cards in one reader would match.</summary>
    public bool Matches(DriveSnapshot d) => d.VolumeSerial == VolumeSerial;
}
