% ════════════════════════════════════════════════════════════════════
% Logic checks (ships with the harness)
%
% problem(Where, Name, Code, Args)
%   Where: service | entity | association   Name: which one
%   Code:  what is wrong                     Args: details for the message
% No answers means the knowledge is consistent.
% ════════════════════════════════════════════════════════════════════

% ── Helpers ─────────────────────────────────────────────────────────

% Parent is (directly or through a chain) composed of Child.
% Tabling makes this recursion safe: it always finishes, even on a cycle.
:- table composed_in/2.
composed_in(Parent, Child) :- association(_, composition, Parent, Child, _).
composed_in(Parent, Child) :- association(_, composition, Parent, X, _), composed_in(X, Child).

% A step of the classification flow can (directly or eventually) lead to another.
:- table flow_reaches/2.
flow_reaches(A, B) :- flow_edge(A, B).
flow_reaches(A, B) :- flow_edge(A, X), flow_reaches(X, B).

% Decisions that can have only one value per service.
single_valued(error_status).
single_valued(persistence_target).
single_valued(route).

% ── Checks ──────────────────────────────────────────────────────────

% A service that creates a composition child must do it within its parent.
problem(service, S, composition_child_outside_parent, [E, P]) :-
    kind(S, create), acts_on(S, E),
    association(_, composition, P, E, _),
    \+ within(S, _).

% Deleting an entity that owns composition children must remove them too.
problem(service, S, delete_parent_without_cascade, [E, C]) :-
    kind(S, delete), acts_on(S, E),
    association(_, composition, E, C, _),
    \+ component(S, cascade_children, _).

% An entity must not end up composed in itself.
problem(entity, E, composition_cycle, []) :-
    entity(E, _), composed_in(E, E).

% Rules must not give one service two different values for a single-valued decision.
problem(service, S, conflicting_decisions, [Decision, A, RuleA, B, RuleB]) :-
    single_valued(Decision),
    Goal1 =.. [Decision, S, A, RuleA], call(Goal1),
    Goal2 =.. [Decision, S, B, RuleB], call(Goal2),
    A @< B.

% The classification flow must not go round in circles…
problem(flow, S, flow_cycle, []) :-
    flow_step(S), flow_reaches(S, S).

% …every step must be reachable from the start…
problem(flow, S, flow_unreachable, []) :-
    flow_step(S), \+ flow_start(S),
    \+ ( flow_start(Start), flow_reaches(Start, S) ).

% …and every step must be able to finish with an outcome.
problem(flow, S, flow_dead_end, []) :-
    flow_step(S),
    \+ ( flow_outcome(O), flow_reaches(S, O) ).
