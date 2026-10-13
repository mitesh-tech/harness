% ════════════════════════════════════════════════════════════════════
% Platform logic checks (ship with the harness; apply to every factory)
%
% problem(Where, Name, Code, Args)
%   Where: service | entity | association | flow   Name: which one
%   Code:  what is wrong                             Args: details for the message
% No answers means the knowledge is consistent.
%
% Factories add their own problem/4 clauses in their checks.pl.
% ════════════════════════════════════════════════════════════════════

:- multifile problem/4.
:- discontiguous problem/4.

% ── Helpers ─────────────────────────────────────────────────────────

% A step of the classification flow can (directly or eventually) lead to another.
:- table flow_reaches/2.
flow_reaches(A, B) :- flow_edge(A, B).
flow_reaches(A, B) :- flow_edge(A, X), flow_reaches(X, B).

% Decisions that can have only one value per service.
single_valued(error_status).
single_valued(persistence_target).
single_valued(route).

% ── Checks ──────────────────────────────────────────────────────────

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

% An operation must be usable on at least one target, or no task can use it.
problem(operation, Op, operation_never_offered, []) :-
    operation(Op),
    \+ ( target(T, _), offered_kind(Op, T, _, _) ).

% A service's kind must be one its operation offers for its target;
% otherwise classification can never lead to it (e.g. a transition on an
% entity without a lifecycle).
problem(service, S, service_kind_not_offered, [K, T, Op]) :-
    kind(S, K), service_target(S, T), operation_kind(Op, K),
    \+ offered_kind(Op, T, K, _).

% An entity and a connection must not share a name in Prolog (BookPublisher
% and book_publisher are both book_publisher), or targets get mixed up.
problem(entity, T, target_name_clash, []) :-
    entity(T, _), association(T, _, _, _, _).
