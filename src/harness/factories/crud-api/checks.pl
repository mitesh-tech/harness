% ════════════════════════════════════════════════════════════════════
% CRUD API factory: logic checks
%
% Adds problem(Where, Name, Code, Args) clauses to the platform checks.
% These are about CRUD on compositions: a child lives inside its parent.
% ════════════════════════════════════════════════════════════════════

:- multifile problem/4.
:- discontiguous problem/4.

% Parent is (directly or through a chain) composed of Child.
% Tabling makes this recursion safe: it always finishes, even on a cycle.
:- table composed_in/2.
composed_in(Parent, Child) :- association(_, composition, Parent, Child, _).
composed_in(Parent, Child) :- association(_, composition, Parent, X, _), composed_in(X, Child).

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

% ── API anatomy ─────────────────────────────────────────────────────

% A list can only be filtered by what it shows (its summary).
problem(service, S, filter_not_in_summary, [A, E]) :-
    filter(S, A), acts_on(S, E), \+ summary_field(E, A).

% Every service gets a path (a transition is named {verb}_{entity}).
problem(service, S, service_without_path, []) :-
    service(S), \+ api_path(S, _, _).

% Two services must not answer the same method and path.
problem(service, S, route_clash, [Other, Method, Path]) :-
    api_path(S, Path, _), method(S, M),
    api_path(Other, Path, _), method(Other, M), S @< Other,
    upcase_atom(M, Method).
