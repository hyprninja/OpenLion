using System.Diagnostics;

namespace OpenLion.Core;

/// <summary>macOS: stops the Mac going to sleep while a long job runs (the screen may still turn off),
/// using the system's caffeinate tool. Dispose to allow sleep again. caffeinate also stops by itself
/// when OpenLion exits. Closing a laptop's lid still puts it to sleep.</summary>
public sealed class KeepAwake : IDisposable
{
    readonly Process? _caffeinate;

    public KeepAwake()
    {
        try
        {
            var psi = new ProcessStartInfo("/usr/bin/caffeinate") { UseShellExecute = false, CreateNoWindow = true };
            foreach (var a in new[] { "-i", "-w", Environment.ProcessId.ToString() })
                psi.ArgumentList.Add(a);
            _caffeinate = Process.Start(psi);
        }
        catch (Exception e) when (e is System.ComponentModel.Win32Exception or InvalidOperationException)
        {
            AppLog.Write($"keep awake: caffeinate couldn't start: {e.Message}");
        }
    }

    public void Dispose()
    {
        if (_caffeinate is null)
            return;
        try
        {
            if (!_caffeinate.HasExited)
                _caffeinate.Kill();
        }
        catch (InvalidOperationException) { }
        _caffeinate.Dispose();
    }
}
