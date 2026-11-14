namespace OpenLion.Core.Drives;

/// <summary>Which inserted drives the Factory card step offers.</summary>
public static class FactoryCards
{
    /// <summary>Splits drives with an iQ navigation folder into possible factory cards and cards
    /// OpenLion wrote itself (by volume serial). Copying an OpenLion card as the "factory card" would
    /// build from OpenLion's own output, so those are never offered.</summary>
    public static (List<DriveSnapshot> Factory, List<DriveSnapshot> OpenLion) Split(
        IEnumerable<DriveSnapshot> drives, IReadOnlySet<uint> writtenByOpenLion)
    {
        var nav = drives.Where(d => d.HasNavigationFolder).ToList();
        return (nav.Where(d => !writtenByOpenLion.Contains(d.VolumeSerial)).ToList(),
                nav.Where(d => writtenByOpenLion.Contains(d.VolumeSerial)).ToList());
    }

    /// <summary>"E: is a card OpenLion wrote, so it isn't offered as a factory card.", or "".</summary>
    public static string OpenLionNote(IReadOnlyList<DriveSnapshot> openLionCards)
    {
        if (openLionCards.Count == 0)
            return "";
        var letters = string.Join(" and ", openLionCards.Select(d => d.DisplayRoot));
        return openLionCards.Count == 1
            ? $"{letters} is a card OpenLion wrote, so it isn't offered as a factory card."
            : $"{letters} are cards OpenLion wrote, so they aren't offered as factory cards.";
    }
}
