using System.ComponentModel;
using System.Windows;
using OpenLion.ViewModels;

namespace OpenLion;

public partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();
        DataContext = new MainViewModel();
        if (OpenLion.Core.Engine.EngineLocator.Locate() is { Bundled: false } dev)
            Title += $" ({dev.Describe()})";
    }

    protected override void OnClosing(CancelEventArgs e)
    {
        if (DataContext is MainViewModel { Current.IsWorking: true })
        {
            var r = MessageBox.Show(this,
                "OpenLion is still working. If you close it now, this step stops and has to start again next time.\n\nClose anyway?",
                "OpenLion", MessageBoxButton.YesNo, MessageBoxImage.Warning, MessageBoxResult.No);
            if (r != MessageBoxResult.Yes)
            {
                e.Cancel = true;
                return;
            }
        }
        base.OnClosing(e);
    }
}
