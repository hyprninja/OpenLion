using Avalonia.Controls;
using Avalonia.Input;
using Avalonia.Interactivity;
using Avalonia.Markup.Xaml;
using Avalonia.Threading;
using OpenLion.Core.Engine;
using OpenLion.ViewModels;

namespace OpenLion;

public partial class MainWindow : Window
{
    bool _closeConfirmed;

    public MainWindow()
    {
        AvaloniaXamlLoader.Load(this);
        DataContext = new MainViewModel();
        if (EngineLocator.Locate() is { Bundled: false } dev)
            Title += $" ({dev.Describe()})";

        // WPF re-checks every command after any click or key press; do the same here, plus twice a second
        // for changes that come from background work.
        AddHandler(PointerReleasedEvent, (_, _) => Platform.RequeryCommands(), RoutingStrategies.Tunnel, handledEventsToo: true);
        AddHandler(KeyUpEvent, (_, _) => Platform.RequeryCommands(), RoutingStrategies.Tunnel, handledEventsToo: true);
        var requery = new DispatcherTimer { Interval = TimeSpan.FromMilliseconds(500) };
        requery.Tick += (_, _) => Platform.RequeryCommands();
        requery.Start();
    }

    protected override void OnClosing(WindowClosingEventArgs e)
    {
        if (!_closeConfirmed && DataContext is MainViewModel { Current.IsWorking: true })
        {
            e.Cancel = true;
            _ = ConfirmCloseAsync();
            return;
        }
        base.OnClosing(e);
    }

    async Task ConfirmCloseAsync()
    {
        var r = await Dialogs.ShowAsync(this,
            "OpenLion is still working. If you close it now, this step stops and has to start again next time.\n\nClose anyway?",
            "Close", "Keep working");
        if (r == 0)
        {
            _closeConfirmed = true;
            Close();
        }
    }
}
