using System.Diagnostics;

namespace OpenLion.Core.Engine;

/// <param name="Python">python.exe (Windows) or python3 (macOS) to run.</param>
/// <param name="EngineRoot">Folder that contains the openlion/ package.</param>
/// <param name="Bundled">True for the copy shipped with the app, false for a developer setup.</param>
public sealed record EnginePaths(string Python, string EngineRoot, bool Bundled)
{
    /// <summary>One line for the log and, for a developer engine, the window title.</summary>
    public string Describe() => Bundled
        ? $"bundled engine at {EngineRoot}"
        : $"developer engine: {Python} with {EngineRoot}";
}

public static class EngineLocator
{
    /// <summary>Oldest Python the engine runs on (developer setups; the app bundles 3.12).</summary>
    public static readonly Version MinPython = new(3, 12);

    /// <summary>The bundled interpreter, relative to the app: runtime\python\python.exe on Windows,
    /// runtime/python/bin/python3 on macOS.</summary>
    static readonly string[] BundledPython = OperatingSystem.IsWindows()
        ? ["runtime", "python", "python.exe"]
        : ["runtime", "python", "bin", "python3"];

    /// <summary>The interpreter's name on PATH.</summary>
    static string PythonOnPath => OperatingSystem.IsWindows() ? "python.exe" : "python3";

    /// <summary>Where the bundled runtime and engine are: next to the app on Windows; in a macOS app,
    /// OpenLion.app/Contents/Resources (the app itself is in Contents/MacOS).</summary>
    static string BundleRoot(string baseDir)
    {
        if (OperatingSystem.IsWindows())
            return baseDir;
        var resources = Path.GetFullPath(Path.Combine(baseDir, "..", "Resources"));
        return Path.GetFileName(Path.TrimEndingDirectorySeparator(baseDir)) == "MacOS" && Directory.Exists(resources)
            ? resources
            : baseDir;
    }

    /// <summary>
    /// Finds the conversion engine. The bundled copy next to the app (runtime\python\python.exe +
    /// engine\openlion) always wins when it is there, so an installed app can't be redirected.
    /// Only without it (a developer build) are the OPENLION_PYTHON / OPENLION_ENGINE environment
    /// variables used, then a developer checkout (an openlion/ folder above the app, with the
    /// first Python 3.12+ on PATH). Null if none is found.
    /// </summary>
    public static EnginePaths? Locate(string? baseDir = null)
    {
        baseDir ??= AppContext.BaseDirectory;
        var bundle = BundleRoot(baseDir);
        var bundledPy = Path.Combine([bundle, .. BundledPython]);
        var bundledEngine = Path.Combine(bundle, "engine");
        if (File.Exists(bundledPy) && HasPackage(bundledEngine))
            return new EnginePaths(bundledPy, bundledEngine, true);

        var envPy = Environment.GetEnvironmentVariable("OPENLION_PYTHON");
        var envEngine = Environment.GetEnvironmentVariable("OPENLION_ENGINE");
        if (File.Exists(envPy) && envEngine is not null && HasPackage(envEngine) && IsSupportedPython(envPy))
            return new EnginePaths(Path.GetFullPath(envPy), Path.GetFullPath(envEngine), false);

        for (var dir = new DirectoryInfo(baseDir); dir is not null; dir = dir.Parent)
        {
            if (HasPackage(dir.FullName))
            {
                var py = FindOnPath(PythonOnPath);
                return py is null ? null : new EnginePaths(py, dir.FullName, false);
            }
        }
        return null;
    }

    /// <summary>True when the app ships its own engine (an installed release).</summary>
    public static bool HasBundledEngine(string? baseDir = null)
    {
        baseDir ??= AppContext.BaseDirectory;
        var bundle = BundleRoot(baseDir);
        return File.Exists(Path.Combine([bundle, .. BundledPython])) &&
               HasPackage(Path.Combine(bundle, "engine"));
    }

    static bool HasPackage(string root) => File.Exists(Path.Combine(root, "openlion", "__init__.py"));

    static string? FindOnPath(string exe) =>
        (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator)
            .Where(d => d.Length > 0 && !d.Contains("WindowsApps", StringComparison.OrdinalIgnoreCase)) // Store stub
            .Select(d => Path.Combine(d.Trim('"'), exe))
            .Where(File.Exists)
            .FirstOrDefault(IsSupportedPython);

    static readonly Dictionary<string, bool> VersionOk = new(StringComparer.OrdinalIgnoreCase);

    /// <summary>Asks the interpreter for its version once per path; false below MinPython or on any error.</summary>
    static bool IsSupportedPython(string python)
    {
        lock (VersionOk)
            if (VersionOk.TryGetValue(python, out var known))
                return known;
        bool ok = false;
        try
        {
            var psi = new ProcessStartInfo(python) { RedirectStandardOutput = true, UseShellExecute = false, CreateNoWindow = true };
            psi.ArgumentList.Add("-c");
            psi.ArgumentList.Add("import sys; print('%d.%d' % sys.version_info[:2])");
            using var p = Process.Start(psi);
            if (p is not null)
            {
                var text = p.StandardOutput.ReadToEnd().Trim();
                ok = p.WaitForExit(10_000) && p.ExitCode == 0 && Version.TryParse(text, out var v) && v >= MinPython;
            }
        }
        catch (Exception e) when (e is System.ComponentModel.Win32Exception or InvalidOperationException or IOException)
        {
            ok = false;
        }
        lock (VersionOk)
            VersionOk[python] = ok;
        return ok;
    }
}

/// <summary>Runs the Python engine as a child process, line by line. Cancelling kills it.</summary>
public static class EngineRunner
{
    public const string StderrPrefix = "[stderr] ";

    static readonly HashSet<Process> Running = [];

    /// <summary>True while any engine process runs.</summary>
    public static bool IsRunning { get { lock (Running) return Running.Count > 0; } }

    /// <summary>Kills every engine process now (and its children). Called when the app exits,
    /// so a build never keeps running in the background without a window.</summary>
    public static void KillAll()
    {
        lock (Running)
        {
            foreach (var p in Running)
            {
                try { p.Kill(entireProcessTree: true); } catch (Exception e) when (e is InvalidOperationException or System.ComponentModel.Win32Exception) { }
            }
        }
    }

    /// <param name="onLine">Called for each output line (stderr lines get <see cref="StderrPrefix"/>),
    /// from a background thread, never twice at once.</param>
    /// <returns>The process exit code.</returns>
    public static async Task<int> RunAsync(EnginePaths engine, IEnumerable<string> args, Action<string> onLine,
        CancellationToken ct = default, ProcessPriorityClass? priority = null)
    {
        var psi = new ProcessStartInfo(engine.Python)
        {
            WorkingDirectory = engine.EngineRoot,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        foreach (var a in new[] { "-X", "utf8", "-u" }.Concat(args))
            psi.ArgumentList.Add(a);
        psi.Environment["PYTHONPATH"] = engine.EngineRoot;
        psi.Environment["PYTHONIOENCODING"] = "utf-8";
        psi.Environment["PYTHONDONTWRITEBYTECODE"] = "1";
        if (!OperatingSystem.IsWindows())
        {
            // Windows' embedded Python ignores these by design (its ._pth file); here they're cleared so
            // the user's own Python settings can't change what the engine runs.
            psi.Environment.Remove("PYTHONHOME");
            psi.Environment.Remove("PYTHONSTARTUP");
            psi.Environment["PYTHONNOUSERSITE"] = "1";
        }

        // stdout and stderr are read on separate threads; deliver lines one at a time.
        var gate = new object();
        void Deliver(string line) { lock (gate) onLine(line); }

        using var p = new Process { StartInfo = psi };
        p.Start();
        lock (Running) Running.Add(p);
        try
        {
            if (priority is { } prio)
            {
                try { p.PriorityClass = prio; } catch (InvalidOperationException) { } // already exited
            }
            var outTask = Pump(p.StandardOutput, Deliver, "");
            var errTask = Pump(p.StandardError, Deliver, StderrPrefix);
            try
            {
                await p.WaitForExitAsync(ct);
            }
            catch (OperationCanceledException)
            {
                try { p.Kill(entireProcessTree: true); } catch (InvalidOperationException) { }
                throw;
            }
            await Task.WhenAll(outTask, errTask);
            return p.ExitCode;
        }
        finally
        {
            lock (Running) Running.Remove(p);
        }
    }

    static async Task Pump(StreamReader r, Action<string> onLine, string prefix)
    {
        string? line;
        while ((line = await r.ReadLineAsync()) is not null)
            onLine(prefix + line);
    }
}
