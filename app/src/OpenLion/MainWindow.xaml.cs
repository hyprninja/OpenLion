using System.Windows;
using OpenLion.ViewModels;

namespace OpenLion;

public partial class MainWindow : Window
{
    public MainWindow()
    {
        InitializeComponent();
        DataContext = new MainViewModel();
    }
}
