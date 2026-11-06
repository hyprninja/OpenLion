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
            new CopyCardStep(s),
            new GetOsmStep(s),
            new HouseNumbersStep(s),
            new BuildMapsStep(s),
            new WriteCardStep(s),
        ];
        foreach (var step in Steps)
            step.PropertyChanged += OnStepChanged;
        var factory = Steps.OfType<FactoryCardStep>().Single();
        int osmIndex = Steps.ToList().FindIndex(st => st is GetOsmStep);
        factory.SkipCopyRequested += () => Go(osmIndex - _index);
        NextCommand = new RelayCommand(() => Go(+1), () => _index < Steps.Count - 1 && Current.CanGoNext);
        BackCommand = new RelayCommand(() => Go(-1), () => _index > 0 && !Current.IsWorking);
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
