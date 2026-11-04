using System.IO.Compression;
using System.Text.Json;
using OpenLion.Core.Drives;
using OpenLion.Core.Engine;
using OpenLion.Core.Reports;

namespace OpenLion.Core.Tests;

public class ErrorReportTests
{
    [Fact]
    public void ReportHoldsMetadataButNoMapData()
    {
        using var t = new TempDir();
        t.File("copy/navigation/cdrom.txt", "Volume: CD_TEST");
        t.File("copy/navigation/db_aunz/cdrom.toc", "toc");
        t.File("copy/navigation/db_aunz/files.cfg", "cfg");
        t.File("copy/navigation/db_aunz/db/au.vi", "vi");
        t.File("copy/navigation/db_aunz/db/zone.cfg", "zone");
        t.File("copy/navigation/db_aunz/db/auz.mp0", "MAPDATA");
        t.File("copy/navigation/db_aunz/db/au.mod", "SCHEMA");
        t.File("copy/navigation/images/x.png", "IMG");
        t.File("copy/navigation/db_aunz/big.cfg", new string('c', 70_000));
        var manifest = t.File("copy/manifest.tsv", "path\tsize\tmtime\tsha256\n");

        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        var parser = new CompatCheck.Parser();
        parser.Feed($$"""{"event": "table", "table": "auz.mp0", "identical": false, "error": "ValueError: x", "traceback": "File {{profile.Replace("\\", "\\\\")}}\\openlion\\mapwrite.py"}""");
        parser.Feed("""{"event": "result", "ok": false, "db": "db_aunz", "failed": ["auz.mp0"], "missing": []}""");
        parser.Feed($"stray line mentioning {profile}\\x");
        var compat = parser.Finish(1, "db_aunz");

        var zipPath = ErrorReport.Write(new ReportInput
        {
            Step = "compatibility check",
            Message = "1 table could not be re-encoded",
            Drive = new DriveSnapshot { Root = @"E:\", Label = "CD_7606", TotalBytes = 4_000_000_000 },
            Card = NavigationCard.Parse("Volume: CD_TEST\n", t.Sub("copy", "navigation")),
            NavigationPath = t.Sub("copy", "navigation"),
            ManifestPath = manifest,
            Compat = compat,
        }, t.Sub("out"));

        Assert.StartsWith("OpenLion-report-CD_7606-", Path.GetFileName(zipPath));
        using var zip = ZipFile.OpenRead(zipPath);
        var names = zip.Entries.Select(e => e.FullName).Order(StringComparer.Ordinal).ToList();
        Assert.Equal([
            "README.txt",
            "card/cdrom.txt", "card/db_aunz/cdrom.toc", "card/db_aunz/db/au.vi", "card/db_aunz/db/zone.cfg",
            "card/db_aunz/files.cfg",
            "engine.log", "manifest.tsv", "summary.json", "tables.json",
        ], names);

        string Text(string n) => new StreamReader(zip.GetEntry(n)!.Open()).ReadToEnd();
        var all = string.Concat(zip.Entries.Select(e => Text(e.FullName)));
        Assert.DoesNotContain("MAPDATA", all);
        Assert.DoesNotContain("SCHEMA", all);
        Assert.DoesNotContain(profile, all, StringComparison.OrdinalIgnoreCase);
        Assert.DoesNotContain(profile.Replace("\\", "\\\\"), all, StringComparison.OrdinalIgnoreCase);
        Assert.Contains("%USERPROFILE%", Text("tables.json"));
        Assert.Contains("%USERPROFILE%", Text("engine.log"));

        using var summary = JsonDocument.Parse(Text("summary.json"));
        Assert.Equal("compatibility check", summary.RootElement.GetProperty("step").GetString());
        Assert.Equal("CD_TEST", summary.RootElement.GetProperty("card").GetProperty("volume").GetString());
        Assert.Equal("auz.mp0", summary.RootElement.GetProperty("compat").GetProperty("failed")[0].GetString());
        JsonDocument.Parse(Text("tables.json")); // valid JSON array
    }

    [Fact]
    public void ReportWithoutCardCopyStillWorks()
    {
        using var t = new TempDir();
        var zipPath = ErrorReport.Write(new ReportInput
        {
            Step = "copy", Message = "card removed", Exception = new IOException("device not ready"),
        }, t.Path);
        using var zip = ZipFile.OpenRead(zipPath);
        Assert.Equal(["README.txt", "summary.json"], zip.Entries.Select(e => e.FullName).Order(StringComparer.Ordinal));
    }
}
