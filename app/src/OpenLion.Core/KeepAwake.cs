using System.Runtime.InteropServices;

namespace OpenLion.Core;

/// <summary>Stops Windows going to sleep while a long job runs (the screen may still turn off).
/// Dispose to allow sleep again. The setting belongs to the thread that set it, so a Dispose
/// on another thread (an await that resumed elsewhere) is sent back to the creating thread.</summary>
public sealed partial class KeepAwake : IDisposable
{
    const uint EsContinuous = 0x80000000, EsSystemRequired = 0x00000001;

    [LibraryImport("kernel32.dll")]
    private static partial uint SetThreadExecutionState(uint flags);

    readonly int _thread = Environment.CurrentManagedThreadId;
    readonly SynchronizationContext? _context = SynchronizationContext.Current;
    bool _disposed;

    public KeepAwake() => SetThreadExecutionState(EsContinuous | EsSystemRequired);

    public void Dispose()
    {
        if (_disposed) return;
        _disposed = true;
        if (Environment.CurrentManagedThreadId == _thread || _context is null)
            SetThreadExecutionState(EsContinuous);
        else
            _context.Post(_ => SetThreadExecutionState(EsContinuous), null);
    }
}
