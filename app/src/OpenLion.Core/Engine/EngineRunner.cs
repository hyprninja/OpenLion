using System.Diagnostics;

namespace OpenLion.Core.Engine;

/// <param name="Python">python.exe to run.</param>
/// <param name="EngineRoot">Folder that contains the openlion/ package.</param>
/// <param name="Bundled">True for the copy shipped with the app, false for a developer setup.</param>
public sealed record EnginePaths(string Python, string EngineRoot, bool Bundled);

public static class EngineLocator
{
    /// <summary>
    /// Finds the conversion engine, in order: the bundled copy next to the app
    /// (runtime\python\python.exe + engine\openlion), the OPENLION_PYTHON / OPENLION_ENGINE
    /// environment variables, then a developer checkout (an openlion/ folder above the app,
    /// with python.exe from PATH). Null if none is found.
    /// </summary>
    public static EnginePaths? Locate(string? baseDir = null)
    {
        baseDir ??= AppContext.BaseDirectory;
        var bundledPy = Path.Combine(baseDir, "runtime", "python", "python.exe");
        var bundledEngine = Path.Combine(baseDir, "engine");
        if (File.Exists(bundledPy) && HasPackage(bundledEngine))
            return new EnginePaths(bundledPy, bundledEngine, true);

        var envPy = Environment.GetEnvironmentVariable("OPENLION_PYTHON");
        var envEngine = Environment.GetEnvironmentVariable("OPENLION_ENGINE");
        if (File.Exists(envPy) && envEngine is not null && HasPackage(envEngine))
            return new EnginePaths(envPy, envEngine, false);

        for (var dir = new DirectoryInfo(baseDir); dir is not null; dir = dir.Parent)
        {
            if (HasPackage(dir.FullName))
            {
                var py = FindOnPath("python.exe");
                return py is null ? null : new EnginePaths(py, dir.FullName, false);
            }
        }
        return null;
    }

    static bool HasPackage(string root) => File.Exists(Path.Combine(root, "openlion", "__init__.py"));

    static string? FindOnPath(string exe) =>
        (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator)
            .Where(d => d.Length > 0 && !d.Contains("WindowsApps", StringComparison.OrdinalIgnoreCase)) // Store stub
            .Select(d => Path.Combine(d.Trim('"'), exe))
            .FirstOrDefault(File.Exists);
}

/// <summary>Runs the Python engine as a child process, line by line. Cancelling kills it.</summary>
public static class EngineRunner
{
    public const string StderrPrefix = "[stderr] ";

    /// <param name="onLine">Called for each output line (stderr lines get <see cref="StderrPrefix"/>),
    /// from a background thread, never twice at once.</param>
    /// <returns>The process exit code.</returns>
    public static async Task<int> RunAsync(EnginePaths engine, IEnumerable<string> args, Action<string> onLine,
        CancellationToken ct = default)
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

        // stdout and stderr are read on separate threads; deliver lines one at a time.
        var gate = new object();
        void Deliver(string line) { lock (gate) onLine(line); }

        using var p = new Process { StartInfo = psi };
        p.Start();
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

    static async Task Pump(StreamReader r, Action<string> onLine, string prefix)
    {
        string? line;
        while ((line = await r.ReadLineAsync()) is not null)
            onLine(prefix + line);
    }
}
