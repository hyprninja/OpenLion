using System.Runtime.CompilerServices;
using OpenLion.Core.Drives;

namespace OpenLion.ViewModels
{
    /// <summary>The view models' Platform for tests: no window, no sounds, no dialogs. The view models
    /// (app/src/OpenLion/ViewModels) are compiled into the tests with it, so the wizard's logic runs
    /// headless on any OS. Same members as the Windows and macOS apps' Platform classes.</summary>
    public static class Platform
    {
        public static event EventHandler? RequerySuggested;
        public static int Requeries { get; private set; }

        public static void RequeryCommands()
        {
            Requeries++;
            RequerySuggested?.Invoke(null, EventArgs.Empty);
        }

        public static IUiTimer CreateTimer(TimeSpan interval, Action tick) => new NoTimer();
        public static void CopyText(string text) { }
        public static void RevealFile(string path) { }
        public static Task<string?> PickFileAsync(string title, string filter) => Task.FromResult<string?>(null);
        public static Stream? OpenVoiceSample(string packId) => null;
        public static void PlaySound(Stream wav) => wav.Dispose();
        public static void StopSound() { }
        public static DriveSnapshot PrepareFactoryCard(DriveSnapshot card) => card;
        public static bool CanEject => false;
        public static void Eject(DriveSnapshot card) => throw new NotSupportedException();

        sealed class NoTimer : IUiTimer
        {
            public void Start() { }
            public void Stop() { }
        }
    }
}

namespace OpenLion.Core.Tests
{
    static class TestSetup
    {
        /// <summary>Tests never touch the real working folder (or its app.log): everything goes to a temp folder.</summary>
#pragma warning disable CA2255 // a module initializer is exactly right for a test assembly
        [ModuleInitializer]
#pragma warning restore CA2255
        internal static void UseTempWorkingFolder() =>
            AppPaths.Root = Path.Combine(Path.GetTempPath(), $"openlion-tests-{Environment.ProcessId}");
    }
}
