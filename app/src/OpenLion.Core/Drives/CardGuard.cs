namespace OpenLion.Core.Drives;

public enum GuardProblem
{
    NoVerifiedFactoryCopy,
    IsTheFactoryCard,
    LooksLikeFactoryCard,
    FactoryCardStillInserted,
    OtherCardsInserted,
    SharesDiskWithOtherVolume,
    NotRemovable,
    SystemDisk,
    UnknownPhysicalDisk,
    WriteProtected,
    TooSmall,
    TooLarge,
    NotEmpty,
    NotFat32,
}

public sealed record GuardFinding(GuardProblem Problem, string Message);

public sealed record GuardResult(DriveSnapshot Target, IReadOnlyList<GuardFinding> Findings)
{
    public bool Allowed => Findings.Count == 0;
}

/// <summary>Options the user can change. Everything else is fixed.</summary>
public sealed record GuardOptions
{
    /// <summary>User ticked "this card has other files and I'm OK losing them".
    /// Never overrides any factory-card rule.</summary>
    public bool AllowNonEmpty { get; init; }

    /// <summary>Volume serials of cards OpenLion itself formatted and wrote. Such a card has a
    /// navigation/ folder but may be rewritten (e.g. after a failed or outdated write).</summary>
    public IReadOnlySet<uint> WrittenByOpenLion { get; init; } = new HashSet<uint>();
}

/// <summary>
/// The single gate every format/write goes through. It refuses unless the target is clearly a
/// spare card and the factory card is out of the reader. Rules are deliberately conservative:
/// a refused blank card is an inconvenience, an erased factory card can't be undone.
/// </summary>
public static class CardGuard
{
    /// <summary>Largest card we will write: Windows can only format FAT32 up to 32 GB,
    /// and anything bigger is more likely an external hard drive than an SD card.</summary>
    public const long MaxCardBytes = 34_400_000_000;

    public static GuardResult CheckTarget(
        DriveSnapshot target,
        IReadOnlyCollection<DriveSnapshot> allDrives,
        FactoryCardRecord? factory,
        long requiredBytes,
        GuardOptions? options = null)
    {
        options ??= new GuardOptions();
        var f = new List<GuardFinding>();
        void Add(GuardProblem p, string m) => f.Add(new GuardFinding(p, m));

        bool writtenByUs = options.WrittenByOpenLion.Contains(target.VolumeSerial);

        if (factory is null || !factory.CopyVerified)
            Add(GuardProblem.NoVerifiedFactoryCopy,
                "Your factory card hasn't been copied and verified yet. Do that first.");

        if (factory is not null && factory.Matches(target))
            Add(GuardProblem.IsTheFactoryCard,
                "This is your factory card (same volume serial as the card you copied). It will never be written to.");
        else if (target.HasNavigationFolder && !writtenByUs)
            Add(GuardProblem.LooksLikeFactoryCard,
                "This card has an iQ navigation folder on it, so it may be a factory card. Use a blank card.");

        var others = allDrives.Where(d => !SameVolume(d, target)).ToList();

        if (others.Any(d => (factory is not null && factory.Matches(d)) ||
                            (d.HasNavigationFolder && !options.WrittenByOpenLion.Contains(d.VolumeSerial))))
            Add(GuardProblem.FactoryCardStillInserted,
                "A factory card is still inserted. Remove it and put it somewhere safe before writing.");

        if (others.Any(d => d.IsRemovable))
            Add(GuardProblem.OtherCardsInserted,
                "More than one removable drive is connected. Leave only the blank card inserted.");

        if (target.PhysicalDiskNumber is null)
            Add(GuardProblem.UnknownPhysicalDisk,
                $"{Os.Name} didn't report which disk this volume is on, so it can't be checked safely.");
        else if (others.Any(d => d.PhysicalDiskNumber == target.PhysicalDiskNumber))
            Add(GuardProblem.SharesDiskWithOtherVolume,
                "This disk has more than one volume on it. Use a card with a single volume.");

        if (!target.IsRemovable)
            Add(GuardProblem.NotRemovable, "This is not a removable card.");

        if (target.IsSystemDisk)
            Add(GuardProblem.SystemDisk, $"This is the disk {Os.Name} runs from.");

        if (target.IsReadOnly)
            Add(GuardProblem.WriteProtected,
                "This card is write-protected. If it's the blank card, slide its lock switch to unlocked.");

        if (target.TotalBytes < requiredBytes)
            Add(GuardProblem.TooSmall,
                $"This card is too small: {Gb(target.TotalBytes)} GB, needs at least {Gb(requiredBytes)} GB.");

        if (target.TotalBytes > MaxCardBytes)
            Add(GuardProblem.TooLarge,
                $"This drive is {Gb(target.TotalBytes)} GB. Use an SD card of 32 GB or less.");

        // The iQ unit reads FAT32 cards (the factory card is FAT32).
        if (!string.Equals(target.FileSystem, "FAT32", StringComparison.OrdinalIgnoreCase))
            Add(GuardProblem.NotFat32, target.FileSystem.Length == 0
                ? $"{Os.Name} didn't report this card's format. It must be FAT32."
                : $"This card is formatted {target.FileSystem}, but the iQ unit needs FAT32. " + FormatHelp(target));

        if (target.RootEntryCount > 0 && !options.AllowNonEmpty && !writtenByUs)
            Add(GuardProblem.NotEmpty,
                "This card has files on it. Use an empty card, or confirm you're OK losing them.");

        return new GuardResult(target, f);
    }

    /// <summary>How to format the card as FAT32 with the operating system's own tools.</summary>
    static string FormatHelp(DriveSnapshot target)
    {
        if (!Os.IsMac)
            return "In File Explorer, right-click the card, choose Format…, pick FAT32 and press Start (this erases the card).";
        // Disk Utility's "MS-DOS (FAT)" picks FAT16 for small cards, so give the exact command too.
        var disk = target.PhysicalDiskNumber is { } n ? $"/dev/disk{n}" : "/dev/diskN";
        return "In Disk Utility, choose the card, click Erase, and pick MS-DOS (FAT) with Master Boot Record. If it " +
               $"still isn't FAT32, run this in Terminal: diskutil eraseDisk FAT32 OPENLION MBRFormat {disk} " +
               "(this erases the card; check the disk number in Disk Utility first).";
    }

    static bool SameVolume(DriveSnapshot a, DriveSnapshot b) =>
        a.VolumeGuidPath is not null && b.VolumeGuidPath is not null
            ? string.Equals(a.VolumeGuidPath, b.VolumeGuidPath, StringComparison.OrdinalIgnoreCase)
            : string.Equals(a.Root, b.Root, StringComparison.OrdinalIgnoreCase);

    static string Gb(long bytes) => (bytes / 1_000_000_000.0).ToString("0.0");
}
