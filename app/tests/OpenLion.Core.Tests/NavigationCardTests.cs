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

    [Theory]
    [InlineData("CD_7172", "MY10 (CD_7172)")]
    [InlineData("CD_7597 (= CD_7606 + CD_7335)", "MY12 (CD_7597)")]
    [InlineData("CD_8217", "MY15 (CD_8217)")]
    [InlineData("CD_8418", "MY17 (CD_8418)")]
    [InlineData("CD_9999", "CD_9999")]
    [InlineData("", null)]
    [InlineData(null, null)]
    public void DisplayNamesUseModelYears(string? volume, string? expected) =>
        Assert.Equal(expected, CardNames.Display(volume));

    [Fact]
    public void ReadsCdromTocWhenThereIsNoCdromTxt()
    {
        // MY15 and MY17 cards have only navigation/db_aunz/cdrom.toc
        var root = Directory.CreateTempSubdirectory("openlion-test").FullName;
        try
        {
            var db = Directory.CreateDirectory(Path.Combine(root, "navigation", "db_aunz")).FullName;
            File.WriteAllText(Path.Combine(db, "cdrom.toc"), """
                Continental Automotive GmbH

                Content

                Volume:            CD_8217
                db:                AUNZ(2014Q1)
                Area:              Australia + New Zealand
                Version:           505.1
                Customer Version:  MY15
                """.Replace("\n", "\r\n"));
            var c = NavigationCard.TryRead(root);
            Assert.NotNull(c);
            Assert.Equal("CD_8217", c.Volume);
            Assert.Equal("MY15 (CD_8217)", c.DisplayName);
            Assert.Equal("MY15", c.CustomerVersion);
            Assert.Equal(new MapDatabase("db_aunz", "AUNZ(2014Q1)", "Australia + New Zealand", "505.1", true), Assert.Single(c.Databases));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }

    [Fact]
    public void VolumeComesFromDbAunzWhenThereAreSeveralDatabases()
    {
        // MY10 cards have db_ag (CD_7173, Arabian Gulf) and db_aunz (CD_7172), no cdrom.txt
        var root = Directory.CreateTempSubdirectory("openlion-test").FullName;
        try
        {
            foreach (var (folder, volume, db) in new[] { ("db_ag", "CD_7173", "AG(2009Q4)"), ("db_aunz", "CD_7172", "AUNZ(2009Q4)") })
            {
                var dir = Directory.CreateDirectory(Path.Combine(root, "navigation", folder)).FullName;
                File.WriteAllText(Path.Combine(dir, "cdrom.toc"), $"Volume:     {volume}\r\ndb:         {db}\r\n");
            }
            var c = NavigationCard.TryRead(root);
            Assert.NotNull(c);
            Assert.Equal("CD_7172", c.Volume);
            Assert.Equal("MY10 (CD_7172)", c.DisplayName);
            Assert.Equal(["db_ag", "db_aunz"], c.Databases.Select(d => d.Folder));
        }
        finally
        {
            Directory.Delete(root, true);
        }
    }
}
