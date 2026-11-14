using System.Diagnostics;
using System.Globalization;
using System.Xml.Linq;

namespace OpenLion.Core.Drives;

public class DiskUtilException(string message) : IOException(message);

/// <summary>Runs macOS's diskutil, the same tool Disk Utility uses, and reads its plist output.
/// Only "info" reads anything; the other commands are the few changes OpenLion asks macOS to make
/// to a card: unmount and mount it again, name it, and eject it.</summary>
public static class DiskUtil
{
    const string Tool = "/usr/sbin/diskutil";

    /// <summary>`diskutil info -plist` for a mount point or device, or null if diskutil doesn't know it
    /// (a network share, or a card pulled out a moment ago).</summary>
    public static Dictionary<string, object>? Info(string volumeOrDevice)
    {
        var (code, output, _) = Run(TimeSpan.FromSeconds(15), "info", "-plist", volumeOrDevice);
        return code == 0 ? ParsePlist(output) : null;
    }

    public static void Unmount(string device) => Must("unmount", device);
    public static void Mount(string device) => Must("mount", device);
    public static void MountReadOnly(string device) => Must("mount", "readOnly", device);
    public static void Eject(string device) => Must("eject", device);

    /// <summary>Sets the volume's name (FAT label). The volume is remounted under its new name.</summary>
    public static void Rename(string device, string name) => Must("rename", device, name);

    static void Must(params string[] args)
    {
        var (code, output, error) = Run(TimeSpan.FromMinutes(2), args);
        if (code != 0)
        {
            var why = (error.Trim().Length > 0 ? error : output).Trim();
            throw new DiskUtilException($"diskutil {args[0]} failed: {why}");
        }
    }

    static (int code, string output, string error) Run(TimeSpan timeout, params string[] args)
    {
        var psi = new ProcessStartInfo(Tool)
        {
            RedirectStandardOutput = true, RedirectStandardError = true, UseShellExecute = false, CreateNoWindow = true,
        };
        foreach (var a in args)
            psi.ArgumentList.Add(a);
        using var p = Process.Start(psi) ?? throw new DiskUtilException("diskutil could not be started.");
        var err = p.StandardError.ReadToEndAsync();
        var output = p.StandardOutput.ReadToEnd();
        if (!p.WaitForExit(timeout))
        {
            try { p.Kill(); } catch (InvalidOperationException) { }
            throw new DiskUtilException($"diskutil {args[0]} didn't finish in {timeout.TotalSeconds:N0} seconds.");
        }
        return (p.ExitCode, output, err.Result);
    }

    /// <summary>Parses an XML property list's top-level dict into strings, longs, bools, lists and
    /// nested dictionaries. Only what diskutil writes is supported.</summary>
    public static Dictionary<string, object> ParsePlist(string xml)
    {
        var doc = XDocument.Parse(xml, LoadOptions.None);
        var dict = doc.Root?.Element("dict") ?? throw new FormatException("Not a property list with a dict.");
        return (Dictionary<string, object>)Value(dict);
    }

    static object Value(XElement e) => e.Name.LocalName switch
    {
        "dict" => Dict(e),
        "array" => e.Elements().Select(Value).ToList(),
        "string" => e.Value,
        "integer" => long.Parse(e.Value, CultureInfo.InvariantCulture),
        "real" => double.Parse(e.Value, CultureInfo.InvariantCulture),
        "true" => true,
        "false" => false,
        "data" => e.Value.Trim(),
        "date" => e.Value,
        _ => e.Value,
    };

    static Dictionary<string, object> Dict(XElement e)
    {
        var d = new Dictionary<string, object>(StringComparer.Ordinal);
        var items = e.Elements().ToList();
        for (int i = 0; i + 1 < items.Count; i += 2)
            if (items[i].Name.LocalName == "key")
                d[items[i].Value] = Value(items[i + 1]);
        return d;
    }

    public static string? Str(this IReadOnlyDictionary<string, object> d, string key) =>
        d.TryGetValue(key, out var v) && v is string s ? s : null;

    public static bool Bool(this IReadOnlyDictionary<string, object> d, string key, bool fallback = false) =>
        d.TryGetValue(key, out var v) && v is bool b ? b : fallback;

    public static long Long(this IReadOnlyDictionary<string, object> d, string key) =>
        d.TryGetValue(key, out var v) && v is long n ? n : 0;
}
