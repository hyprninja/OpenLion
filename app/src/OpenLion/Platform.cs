using System.Diagnostics;
using System.IO;
using System.Media;
using System.Windows;
using System.Windows.Input;
using System.Windows.Threading;
using Microsoft.Win32;
using OpenLion.Core.Drives;

namespace OpenLion.ViewModels;

/// <summary>
/// What the view models need from the app around them, on Windows (WPF). The view models in
/// ViewModels/ are shared with the macOS app, which has its own Platform class with the same members
/// (app/src/OpenLion.Mac/Platform.cs). Add a member to both.
/// </summary>
public static class Platform
{
    /// <summary>Raised when commands should check again whether they can run.</summary>
    public static event EventHandler? RequerySuggested
    {
        add => CommandManager.RequerySuggested += value;
        remove => CommandManager.RequerySuggested -= value;
    }

    public static void RequeryCommands() => CommandManager.InvalidateRequerySuggested();

    /// <summary>A timer that ticks on the UI thread.</summary>
    public static IUiTimer CreateTimer(TimeSpan interval, Action tick) => new Timer(interval, tick);

    public static void CopyText(string text) => Clipboard.SetText(text);

    /// <summary>Shows the file in File Explorer.</summary>
    public static void RevealFile(string path) => Process.Start("explorer.exe", $"/select,\"{path}\"");

    /// <param name="filter">"OpenStreetMap extract (*.osm.pbf)|*.osm.pbf|All files|*.*"</param>
    /// <returns>The chosen file, or null.</returns>
    public static Task<string?> PickFileAsync(string title, string filter)
    {
        var dlg = new OpenFileDialog { Title = title, Filter = filter };
        return Task.FromResult(dlg.ShowDialog() == true ? dlg.FileName : null);
    }

    /// <summary>The sample bundled with the app for a voice pack, or null.</summary>
    public static Stream? OpenVoiceSample(string packId) =>
        Application.GetResourceStream(new Uri($"pack://application:,,,/Assets/Voices/{packId}-sample.wav"))?.Stream;

    static readonly SoundPlayer Player = new();

    public static void PlaySound(Stream wav)
    {
        Player.Stop();
        Player.Stream = wav;
        Player.Play();
    }

    public static void StopSound() => Player.Stop();

    /// <summary>Gets the factory card ready to be copied. Nothing to do on Windows.</summary>
    public static DriveSnapshot PrepareFactoryCard(DriveSnapshot card) => card;

    /// <summary>Whether the app can eject a card itself. On Windows the user ejects it in File Explorer.</summary>
    public static bool CanEject => false;

    public static void Eject(DriveSnapshot card) => throw new NotSupportedException();

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
