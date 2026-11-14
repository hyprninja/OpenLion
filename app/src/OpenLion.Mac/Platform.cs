using System.Diagnostics;
using Avalonia;
using Avalonia.Controls;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Input.Platform;
using Avalonia.Platform;
using Avalonia.Platform.Storage;
using Avalonia.Threading;
using OpenLion.Core;
using OpenLion.Core.Drives;

namespace OpenLion.ViewModels;

/// <summary>
/// What the view models need from the app around them, on macOS (Avalonia). The Windows app has its
/// own Platform class with the same members (app/src/OpenLion/Platform.cs). Add a member to both.
/// </summary>
public static class Platform
{
    /// <summary>Raised when commands should check again whether they can run. WPF does this by itself
    /// after any input; MainWindow does it here, and so does every RequeryCommands call.</summary>
    public static event EventHandler? RequerySuggested;

    public static void RequeryCommands()
    {
        if (Dispatcher.UIThread.CheckAccess())
            RequerySuggested?.Invoke(null, EventArgs.Empty);
        else
            Dispatcher.UIThread.Post(() => RequerySuggested?.Invoke(null, EventArgs.Empty));
    }

    /// <summary>A timer that ticks on the UI thread.</summary>
    public static IUiTimer CreateTimer(TimeSpan interval, Action tick) => new Timer(interval, tick);

    static TopLevel? Window =>
        (Application.Current?.ApplicationLifetime as IClassicDesktopStyleApplicationLifetime)?.MainWindow;

    public static void CopyText(string text)
    {
        if (Window?.Clipboard is { } clipboard)
            _ = clipboard.SetTextAsync(text);
    }

    /// <summary>Shows the file in Finder.</summary>
    public static void RevealFile(string path) => Process.Start("/usr/bin/open", ["-R", path]);

    /// <param name="filter">"OpenStreetMap extract (*.osm.pbf)|*.osm.pbf|All files|*.*", as on Windows.</param>
    /// <returns>The chosen file, or null.</returns>
    public static async Task<string?> PickFileAsync(string title, string filter)
    {
        if (Window is not { } window)
            return null;
        var files = await window.StorageProvider.OpenFilePickerAsync(new FilePickerOpenOptions
        {
            Title = title,
            AllowMultiple = false,
            FileTypeFilter = FileTypes(filter),
        });
        return files.Count > 0 ? files[0].TryGetLocalPath() : null;
    }

    /// <summary>A Windows-style filter ("Name (*.zip)|*.zip|All files|*.*") as macOS file types.</summary>
    internal static List<FilePickerFileType> FileTypes(string filter)
    {
        var parts = filter.Split('|');
        var types = new List<FilePickerFileType>();
        for (int i = 0; i + 1 < parts.Length; i += 2)
            types.Add(new FilePickerFileType(parts[i]) { Patterns = parts[i + 1].Split(';') });
        return types;
    }

    /// <summary>The sample bundled with the app for a voice pack, or null.</summary>
    public static Stream? OpenVoiceSample(string packId)
    {
        var uri = new Uri($"avares://OpenLion/Assets/Voices/{packId}-sample.wav");
        return AssetLoader.Exists(uri) ? AssetLoader.Open(uri) : null;
    }

    static Process? _player;

    /// <summary>Plays a WAV with macOS's afplay, stopping any sample still playing.</summary>
    public static void PlaySound(Stream wav)
    {
        StopSound();
        var file = Path.Combine(Path.GetTempPath(), "openlion-sample.wav");
        try
        {
            using (var f = File.Create(file))
                wav.CopyTo(f);
            _player = Process.Start(new ProcessStartInfo("/usr/bin/afplay", [file]) { UseShellExecute = false });
        }
        catch (System.ComponentModel.Win32Exception e)
        {
            throw new InvalidOperationException(e.Message, e);
        }
        finally
        {
            wav.Dispose();
        }
    }

    public static void StopSound()
    {
        try
        {
            if (_player is { HasExited: false })
                _player.Kill();
        }
        catch (InvalidOperationException) { }
        _player?.Dispose();
        _player = null;
    }

    /// <summary>Mounts an unlocked factory card read-only before it is copied, so nothing (Spotlight,
    /// Finder) can change it while OpenLion reads it. A locked card is already read-only. If macOS won't
    /// unmount it (a Finder window open on it), the copy goes ahead: OpenLion itself only reads.</summary>
    public static DriveSnapshot PrepareFactoryCard(DriveSnapshot card)
    {
        if (card.IsReadOnly || card.DeviceNode is null)
            return card;
        try
        {
            var ro = MacCards.Remount(card, readOnly: true);
            AppLog.Write($"copy: mounted {card} read-only at {ro.Root}");
            return ro;
        }
        catch (IOException e)
        {
            AppLog.Write($"copy: couldn't mount {card} read-only ({e.Message}); copying as it is");
            return MacCards.Find(card.DeviceNode, card.VolumeSerial) ?? card;
        }
    }

    /// <summary>Whether the app can eject a card itself (it can on macOS).</summary>
    public static bool CanEject => true;

    public static void Eject(DriveSnapshot card) => MacCards.Eject(card);

    sealed class Timer : IUiTimer
    {
        readonly DispatcherTimer _timer;

        public Timer(TimeSpan interval, Action tick)
        {
            _timer = new DispatcherTimer { Interval = interval };
            _timer.Tick += (_, _) => tick();
        }

        public void Start() => _timer.Start();
        public void Stop() => _timer.Stop();
    }
}
