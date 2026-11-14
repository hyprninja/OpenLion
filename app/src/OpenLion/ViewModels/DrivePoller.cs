using OpenLion.Core.Drives;

namespace OpenLion.ViewModels;

/// <summary>Re-reads the drive list every 2 s on a background thread (a slow card reader must
/// not freeze the window) and hands the result to the UI thread.</summary>
public sealed class DrivePoller
{
    readonly IUiTimer _timer;
    bool _busy;

    public event Action<IReadOnlyList<DriveSnapshot>>? Updated;

    public DrivePoller() => _timer = Platform.CreateTimer(TimeSpan.FromSeconds(2), async () => await PollAsync());

    public void Start()
    {
        _timer.Start();
        _ = PollAsync();
    }

    public void Stop() => _timer.Stop();

    async Task PollAsync()
    {
        if (_busy)
            return;
        _busy = true;
        try
        {
            var drives = await Task.Run(DriveService.GetDrives);
            Updated?.Invoke(drives);
        }
        finally
        {
            _busy = false;
        }
    }
}
