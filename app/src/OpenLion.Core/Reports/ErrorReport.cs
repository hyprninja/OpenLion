using System.IO.Compression;
using System.Reflection;
using System.Text;
using System.Text.Encodings.Web;
using System.Text.Json;
using System.Text.RegularExpressions;
using OpenLion.Core.Drives;
using OpenLion.Core.Engine;

namespace OpenLion.Core.Reports;

/// <summary>Everything known about a failed step. All fields optional except Step and Message.</summary>
public sealed record ReportInput
{
    /// <summary>Wizard step that failed, e.g. "compatibility check", "copy".</summary>
    public required string Step { get; init; }
    public required string Message { get; init; }
    public DriveSnapshot? Drive { get; init; }
    public NavigationCard? Card { get; init; }
    /// <summary>navigation/ folder of the PC copy (metadata files are taken from here).</summary>
    public string? NavigationPath { get; init; }
    public string? ManifestPath { get; init; }
    public CompatResult? Compat { get; init; }
    public IReadOnlyList<string>? EngineLog { get; init; }
    public Exception? Exception { get; init; }
}

/// <summary>
/// Writes a zip that helps fix OpenLion for a card it doesn't support, without any map data:
/// it is meant to be attached to a public GitHub issue.
/// </summary>
public static partial class ErrorReport
{
    public const string IssueUrl = "https://github.com/hyprninja/OpenLion/issues/new?template=card_report.yml";

    /// <summary>Small text files describing the card's version and layout. Nothing else from the
    /// card goes into a report.</summary>
    static readonly string[] MetadataNames = ["cdrom.txt", "cdrom.toc", "files.cfg", "zone.cfg", "versions.cfg"];
    const long MaxMetadataBytes = 64 * 1024;

    static readonly JsonSerializerOptions Json = new()
    {
        WriteIndented = true,
        Encoder = JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
    };

    public static string AppVersion =>
        typeof(ErrorReport).Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()?.InformationalVersion ?? "dev";

    public static string DefaultFolder => Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory);

    /// <returns>Path of the written zip.</returns>
    public static string Write(ReportInput input, string? folder = null)
    {
        folder ??= DefaultFolder;
        Directory.CreateDirectory(folder);
        var label = Safe(input.Drive?.Label is { Length: > 0 } l ? l : "card");
        var path = Path.Combine(folder, $"OpenLion-report-{label}-{DateTime.Now:yyyyMMdd-HHmmss}.zip");

        var contents = new List<(string name, string why)>();
        using (var zip = ZipFile.Open(path, ZipArchiveMode.Create))
        {
            AddText(zip, "summary.json", JsonSerializer.Serialize(Summary(input), Json));
            contents.Add(("summary.json", "what failed, app version, Windows version, memory and disk space"));

            int meta = 0;
            if (input.NavigationPath is { } nav && Directory.Exists(nav))
            {
                foreach (var f in MetadataFiles(nav))
                {
                    zip.CreateEntryFromFile(f, "card/" + Path.GetRelativePath(nav, f).Replace('\\', '/'));
                    meta++;
                }
            }
            if (meta > 0)
                contents.Add(("card/", "the card's version and layout files (cdrom.txt, cdrom.toc, *.cfg, *.vi)"));

            if (input.ManifestPath is { } mp && File.Exists(mp))
            {
                zip.CreateEntryFromFile(mp, "manifest.tsv");
                contents.Add(("manifest.tsv", "name, size, date and SHA-256 checksum of every file on the card (no contents)"));
            }

            if (input.Compat is { Tables.Count: > 0 } c)
            {
                var tables = "[\n" + string.Join(",\n", c.Tables.Select(t => Sanitize(t.Json))) + "\n]\n";
                AddText(zip, "tables.json", tables);
                contents.Add(("tables.json", "per map table: header fields, record counts and where the re-encoded table first differs"));
            }

            var log = input.EngineLog ?? input.Compat?.Log;
            if (log is { Count: > 0 })
            {
                AddText(zip, "engine.log", string.Join("\n", log.Select(Sanitize)) + "\n");
                contents.Add(("engine.log", "output of the map engine"));
            }

            AddText(zip, "README.txt", Readme(contents));
        }
        return path;
    }

    static object Summary(ReportInput i) => new
    {
        openlion = AppVersion,
        created = DateTime.UtcNow.ToString("u"),
        step = i.Step,
        message = Sanitize(i.Message),
        error = i.Exception is null ? null : Sanitize(i.Exception.ToString()),
        compat = i.Compat is null ? null : new
        {
            ok = i.Compat.Ok,
            database = i.Compat.Database,
            failed = i.Compat.Failed,
            missing = i.Compat.Missing,
            engine_error = i.Compat.EngineError is null ? null : Sanitize(i.Compat.EngineError),
            exit_code = i.Compat.ExitCode,
            tables = i.Compat.Tables.Select(t => new { t.Table, t.Identical, t.Error }),
        },
        card = new
        {
            label = i.Drive?.Label,
            size_bytes = i.Drive?.TotalBytes,
            volume = i.Card?.Volume,
            customer_version = i.Card?.CustomerVersion,
            databases = i.Card?.Databases.Select(d => new { d.Folder, d.Name, d.Area, d.Version }),
        },
        system = new
        {
            windows = Environment.OSVersion.VersionString,
            dotnet = Environment.Version.ToString(),
            cpus = Environment.ProcessorCount,
            memory_gb = Math.Round(GC.GetGCMemoryInfo().TotalAvailableMemoryBytes / 1e9, 1),
            free_disk_gb = FreeDiskGb(),
        },
    };

    static double? FreeDiskGb()
    {
        try
        {
            var root = Path.GetPathRoot(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData))!;
            return Math.Round(new DriveInfo(root).AvailableFreeSpace / 1e9, 1);
        }
        catch (IOException) { return null; }
    }

    /// <summary>Whitelisted small metadata files under navigation/.</summary>
    public static IEnumerable<string> MetadataFiles(string navigationPath) =>
        Directory.EnumerateFiles(navigationPath, "*", SearchOption.AllDirectories)
            .Where(f =>
            {
                var name = Path.GetFileName(f);
                bool wanted = MetadataNames.Contains(name, StringComparer.OrdinalIgnoreCase) ||
                              name.EndsWith(".vi", StringComparison.OrdinalIgnoreCase);
                return wanted && new FileInfo(f).Length <= MaxMetadataBytes;
            })
            .Order(StringComparer.Ordinal);

    static string Readme(List<(string name, string why)> contents)
    {
        var sb = new StringBuilder();
        sb.AppendLine("OpenLion error report");
        sb.AppendLine("=====================");
        sb.AppendLine();
        sb.AppendLine("This report helps the OpenLion developers make OpenLion work with your card.");
        sb.AppendLine("Attach it to a new issue at:");
        sb.AppendLine("  " + IssueUrl);
        sb.AppendLine();
        sb.AppendLine("GitHub issues are public. This zip contains NO map data, images, voices or");
        sb.AppendLine("programs from your card, and your Windows user folder is replaced by %USERPROFILE%.");
        sb.AppendLine("It contains only:");
        sb.AppendLine();
        foreach (var (name, why) in contents)
            sb.AppendLine($"  {name,-14} {why}");
        sb.AppendLine("  README.txt     this file");
        sb.AppendLine();
        sb.AppendLine("You can open the zip and check every file before sharing it.");
        return sb.ToString();
    }

    /// <summary>Replaces the user's profile folder and user name in text that leaves the PC.</summary>
    public static string Sanitize(string text)
    {
        var profile = Environment.GetFolderPath(Environment.SpecialFolder.UserProfile);
        if (profile.Length > 3)
        {
            text = text.Replace(profile, "%USERPROFILE%", StringComparison.OrdinalIgnoreCase)
                       .Replace(profile.Replace("\\", "\\\\"), "%USERPROFILE%", StringComparison.OrdinalIgnoreCase)
                       .Replace(profile.Replace('\\', '/'), "%USERPROFILE%", StringComparison.OrdinalIgnoreCase);
        }
        // Any other profile path: other separators, doubled backslashes, 8.3 short names (SIMON~1).
        text = UsersPath().Replace(text, "%USERPROFILE%");
        var user = Environment.UserName;
        if (user.Length >= 3)
            text = Regex.Replace(text, $@"(?<![\p{{L}}\p{{N}}_]){Regex.Escape(user)}(?![\p{{L}}\p{{N}}_])", "%USERNAME%",
                RegexOptions.IgnoreCase);
        return text;
    }

    /// <summary>A drive letter, "Users" and the next folder name, with \, \\ or / between.</summary>
    [GeneratedRegex(@"[A-Za-z]:(?:\\{1,2}|/)Users(?:\\{1,2}|/)[^\\/\s""':;|<>]+", RegexOptions.IgnoreCase)]
    private static partial Regex UsersPath();

    static void AddText(ZipArchive zip, string name, string text)
    {
        using var w = new StreamWriter(zip.CreateEntry(name).Open(), new UTF8Encoding(false));
        w.Write(text);
    }

    static string Safe(string s) => new(s.Select(c => char.IsLetterOrDigit(c) || c is '-' or '_' ? c : '_').ToArray());
}
