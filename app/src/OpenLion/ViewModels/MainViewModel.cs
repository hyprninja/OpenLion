using System.ComponentModel;
using System.Windows.Input;

namespace OpenLion.ViewModels;

public sealed class MainViewModel : ObservableObject
{
    int _index;

    public IReadOnlyList<StepViewModel> Steps { get; }
    public StepViewModel Current => Steps[_index];
    public int Index => _index;
    public ICommand NextCommand { get; }
    public ICommand BackCommand { get; }

    public MainViewModel()
    {
        var s = new Session();
        Steps =
        [
            new WelcomeStep(),
            new FactoryCardStep(s),
            new PlaceholderStep("Copy card",
                "Coming next: copy the factory card to this PC (read-only), check every file, " +
                "and check that OpenLion understands this card's map format."),
            new PlaceholderStep("Get OSM data",
                "Coming next: download the latest OpenStreetMap extract for Australia from Geofabrik."),
            new PlaceholderStep("Build maps",
                "Coming next: convert the OpenStreetMap data into the iQ map format."),
            new WriteCardStep(s),
        ];
        foreach (var step in Steps)
            step.PropertyChanged += OnStepChanged;
        NextCommand = new RelayCommand(() => Go(+1), () => _index < Steps.Count - 1 && Current.CanGoNext);
        BackCommand = new RelayCommand(() => Go(-1), () => _index > 0);
        Current.IsCurrent = true;
        Current.OnEnter();
    }

    void OnStepChanged(object? sender, PropertyChangedEventArgs e) => CommandManager.InvalidateRequerySuggested();

    void Go(int delta)
    {
        Current.OnLeave();
        Current.IsCurrent = false;
        _index += delta;
        Current.IsCurrent = true;
        Raise(nameof(Current));
        Raise(nameof(Index));
        Current.OnEnter();
    }
}
