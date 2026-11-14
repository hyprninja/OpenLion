using System.Text;

namespace OpenLion.Core.Flow;

/// <summary>Anything that declares its states and moves, so tests and docs/28_app_states.md can list it.</summary>
public interface IStateFlow
{
    string Name { get; }
    string Description { get; }
    IReadOnlyList<string> StateNames { get; }
    string InitialName { get; }
    IReadOnlyList<(string From, string To)> MoveNames { get; }
    /// <summary>States the flow can't move on from (normal for an end state, a bug anywhere else).</summary>
    IReadOnlyList<string> Terminal { get; }
    /// <summary>States no chain of moves reaches from the initial state.</summary>
    IReadOnlyList<string> Unreachable { get; }
    /// <summary>The flow as a Mermaid state diagram.</summary>
    string ToMermaid();
}

/// <summary>
/// A state machine's definition: its states, where it starts, and the moves allowed between them.
/// One table that the code runs from, tests check, and the docs are generated from.
/// </summary>
public sealed class StateFlow<TState> : IStateFlow where TState : struct, Enum
{
    readonly Dictionary<TState, HashSet<TState>> _moves = [];

    /// <param name="name">Short name used in the log ("copy", "build").</param>
    /// <param name="moves">From each state, the states it may move to.</param>
    public StateFlow(string name, string description, TState initial, params (TState From, TState[] To)[] moves)
    {
        Name = name;
        Description = description;
        Initial = initial;
        foreach (var s in Enum.GetValues<TState>())
            _moves[s] = [];
        foreach (var (from, to) in moves)
            _moves[from].UnionWith(to);
    }

    public string Name { get; }
    public string Description { get; }
    public TState Initial { get; }

    public bool Allows(TState from, TState to) => _moves[from].Contains(to);
    public IReadOnlySet<TState> From(TState state) => _moves[state];

    public IReadOnlyList<string> StateNames => Enum.GetNames<TState>();
    public string InitialName => Initial.ToString();
    public IReadOnlyList<(string From, string To)> MoveNames =>
        [.. _moves.SelectMany(kv => kv.Value.Order().Select(to => (kv.Key.ToString(), to.ToString())))];
    public IReadOnlyList<string> Terminal => [.. _moves.Where(kv => kv.Value.Count == 0).Select(kv => kv.Key.ToString())];

    public IReadOnlyList<string> Unreachable
    {
        get
        {
            var seen = new HashSet<TState> { Initial };
            var todo = new Queue<TState>([Initial]);
            while (todo.TryDequeue(out var s))
                foreach (var next in _moves[s])
                    if (seen.Add(next))
                        todo.Enqueue(next);
            return [.. Enum.GetValues<TState>().Where(s => !seen.Contains(s)).Select(s => s.ToString())];
        }
    }

    public string ToMermaid()
    {
        var sb = new StringBuilder("stateDiagram-v2\n");
        sb.Append($"    [*] --> {Initial}\n");
        foreach (var (from, to) in MoveNames)
            sb.Append($"    {from} --> {to}\n");
        return sb.ToString();
    }
}

/// <summary>
/// A running state machine. Every move is logged ("state: copy Copying -> Checking: why"). A move the
/// flow doesn't declare is still made, and logged as UNDECLARED: the table documents and tests the
/// flow, but must never stop the person using the app.
/// </summary>
public sealed class StateMachine<TState> where TState : struct, Enum
{
    readonly Action<string> _log;

    /// <param name="name">Name in the log, when one flow runs in several places ("osm", "voice");
    /// the flow's own name otherwise.</param>
    public StateMachine(StateFlow<TState> flow, string? name = null, Action<string>? log = null)
    {
        Flow = flow;
        Name = name ?? flow.Name;
        State = flow.Initial;
        _log = log ?? AppLog.Write;
    }

    public StateFlow<TState> Flow { get; }
    public string Name { get; }
    public TState State { get; private set; }

    /// <summary>Raised after every move, with the state it came from.</summary>
    public event Action<TState>? Moved;

    public bool CanMove(TState to) => Flow.Allows(State, to);

    /// <summary>Moves to <paramref name="to"/>; returns false if it is already there.</summary>
    public bool Move(TState to, string? why = null)
    {
        var from = State;
        if (EqualityComparer<TState>.Default.Equals(from, to))
            return false;
        var declared = Flow.Allows(from, to);
        State = to;
        _log($"state: {Name} {from} -> {to}{(declared ? "" : " (UNDECLARED)")}{(why is null ? "" : ": " + why)}");
        Moved?.Invoke(from);
        return true;
    }
}
