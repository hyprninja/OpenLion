using OpenLion.Core.Drives;

namespace OpenLion.Core.Tests;

public class NavigationCardTests
{
    // Layout of a real cdrom.txt (values made up apart from the format)
    const string Sample = """
        Continental Automotive GmbH
        I IC RD G SW NM DB

        Content

        Volume:            CD_1111 (= CD_2222 + CD_3333)
        Customer Version:  GMH 2011
        Customer:          123
        Extras:            voices included

        Sub Volume 1:      CD_2222
        Date:              1.1.2011
        db_aunz:           AUNZ(2010Q3)
        Area:              Australia + New Zealand
        Version:           505.1

        Sub Volume 2:      CD_3333
        db_ag:             AG(2009Q4)
        Area:              Arabian Gulf
        Version:           505.2

        For detailed information please look into the cdrom.toc files
        """;

    [Fact]
    public void ParsesVolumeAndDatabases()
    {
        var c = NavigationCard.Parse(Sample.Replace("\n", "\r\n"), Path.GetTempPath());
        Assert.Equal("CD_1111 (= CD_2222 + CD_3333)", c.Volume);
        Assert.Equal("GMH 2011", c.CustomerVersion);
        Assert.Equal(2, c.Databases.Count);
        Assert.Equal(new MapDatabase("db_aunz", "AUNZ(2010Q3)", "Australia + New Zealand", "505.1", false), c.Databases[0]);
        Assert.Equal("Arabian Gulf", c.Databases[1].Area);
    }

    [Fact]
    public void DetectsMarkersAndReadsFromDisk()
    {
        var root = Directory.CreateTempSubdirectory("openlion-test").FullName;
        try
        {
            Assert.False(NavigationCard.HasMarkers(root));
            Assert.Null(NavigationCard.TryRead(root));
            var nav = Directory.CreateDirectory(Path.Combine(root, "navigation")).FullName;
            Directory.CreateDirectory(Path.Combine(nav, "db_aunz"));
            File.WriteAllText(Path.Combine(nav, "cdrom.txt"), Sample);
            Assert.True(NavigationCard.HasMarkers(root));
            var c = NavigationCard.TryRead(root)!;
            Assert.True(c.Databases[0].FolderExists);
            Assert.False(c.Databases[1].FolderExists);
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public void TocInDatabaseFolderAlsoCountsAsMarker()
    {
        var root = Directory.CreateTempSubdirectory("openlion-test").FullName;
        try
        {
            var db = Directory.CreateDirectory(Path.Combine(root, "navigation", "db_aunz")).FullName;
            File.WriteAllText(Path.Combine(db, "cdrom.toc"), "x");
            Assert.True(NavigationCard.HasMarkers(root));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }
}
