using Avalonia;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Markup.Xaml;
using Avalonia.Threading;
using OpenLion.Core;
using OpenLion.Core.Engine;
using OpenLion.Core.Reports;

namespace OpenLion;

public partial class App : Application
{
    public override void Initialize() => AvaloniaXamlLoader.Load(this);

    public override void OnFrameworkInitializationCompleted()
    {
        AppLog.Write($"start {ErrorReport.AppVersion} on {System.Runtime.InteropServices.RuntimeInformation.OSDescription}");
        LogEnvironment();
        Dispatcher.UIThread.UnhandledException += OnUnhandled;
        AppDomain.CurrentDomain.UnhandledException += (_, a) => AppLog.Write($"fatal: {a.ExceptionObject}");
        TaskScheduler.UnobservedTaskException += (_, a) => AppLog.Write($"unobserved task error: {a.Exception}");

        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            desktop.MainWindow = new MainWindow();
            desktop.Exit += (_, e) =>
            {
                AppLog.Write($"exit, code {e.ApplicationExitCode}");
                EngineRunner.KillAll();
            };
        }
        base.OnFrameworkInitializationCompleted();
    }

    /// <summary>What a bug report needs to show which code ran: engine and data folder.</summary>
    static void LogEnvironment()
    {
        try
        {
            AppLog.Write($"app: {Environment.ProcessPath}");
            var engine = EngineLocator.Locate();
            AppLog.Write(engine is null ? "engine: not found" : $"engine: {engine.Describe()}");
            AppLog.Write($"data folder: {AppPaths.Root}");
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException or InvalidOperationException
                                       or System.ComponentModel.Win32Exception)
        {
            AppLog.Write($"startup checks failed: {ex.Message}");
        }
    }

    /// <summary>Last resort: save a report and tell the user, instead of the window just vanishing.</summary>
    void OnUnhandled(object? sender, DispatcherUnhandledExceptionEventArgs e)
    {
        AppLog.Write($"unhandled: {e.Exception}");
        e.Handled = true;
        string where;
        try
        {
            where = "A report was saved to:\n" + ErrorReport.Write(new ReportInput
            {
                Step = "app", Message = "Unexpected error", Exception = e.Exception,
            });
        }
        catch (Exception re) when (re is IOException or UnauthorizedAccessException)
        {
            where = "The error report couldn't be saved.";
        }
        EngineRunner.KillAll();
        var desktop = ApplicationLifetime as IClassicDesktopStyleApplicationLifetime;
        _ = Dialogs.ShowAsync(desktop?.MainWindow,
                $"OpenLion hit an unexpected error and has to close. Your factory card has not been changed.\n\n" +
                $"{e.Exception.Message}\n\n{where}\n\nPlease report it at github.com/hyprninja/OpenLion/issues.", "OK")
            .ContinueWith(_ => Dispatcher.UIThread.Post(() => desktop?.Shutdown(1)));
    }
}
