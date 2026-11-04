using System.Runtime.InteropServices;

namespace OpenLion.Core;

/// <summary>Stops Windows going to sleep while a long job runs (the screen may still turn off).
/// Dispose to allow sleep again. Must be created and disposed on the same thread.</summary>
public sealed partial class KeepAwake : IDisposable
{
    const uint EsContinuous = 0x80000000, EsSystemRequired = 0x00000001;

    [LibraryImport("kernel32.dll")]
    private static partial uint SetThreadExecutionState(uint flags);

    bool _disposed;

    public KeepAwake() => SetThreadExecutionState(EsContinuous | EsSystemRequired);

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        SetThreadExecutionState(EsContinuous);
    }
}
