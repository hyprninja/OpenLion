using System.IO;
using System.Windows;
using System.Windows.Threading;
using OpenLion.Core;
using OpenLion.Core.Reports;

namespace OpenLion;

public partial class App : Application
{
    protected override void OnStartup(StartupEventArgs e)
    {
        AppLog.Write($"start {ErrorReport.AppVersion}");
        DispatcherUnhandledException += OnUnhandled;
        AppDomain.CurrentDomain.UnhandledException += (_, a) => AppLog.Write($"fatal: {a.ExceptionObject}");
        TaskScheduler.UnobservedTaskException += (_, a) => AppLog.Write($"unobserved task error: {a.Exception}");
        SessionEnding += (_, a) => AppLog.Write($"Windows session ending: {a.ReasonSessionEnding}");
        base.OnStartup(e);
    }

    protected override void OnExit(ExitEventArgs e)
    {
        AppLog.Write($"exit, code {e.ApplicationExitCode}");
        OpenLion.Core.Engine.EngineRunner.KillAll();
        base.OnExit(e);
    }

    /// <summary>Last resort: save a report and tell the user, instead of the window just vanishing.</summary>
    static void OnUnhandled(object sender, DispatcherUnhandledExceptionEventArgs e)
    {
        AppLog.Write($"unhandled: {e.Exception}");
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
        MessageBox.Show(
            $"OpenLion hit an unexpected error and has to close. Your factory card has not been changed.\n\n" +
            $"{e.Exception.Message}\n\n{where}\n\nPlease report it at github.com/hyprninja/OpenLion/issues.",
            "OpenLion", MessageBoxButton.OK, MessageBoxImage.Error);
        e.Handled = true;
        OpenLion.Core.Engine.EngineRunner.KillAll();
        Current.Shutdown(1);
    }
}
