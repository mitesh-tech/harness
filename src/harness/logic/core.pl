% ════════════════════════════════════════════════════════════════════
% Core decisions (ships with the harness)
% What follows directly from a service's kind.
% ════════════════════════════════════════════════════════════════════

method(S, M)      :- kind(S, K), kind_method(K, M).
status(S, Code)   :- kind(S, K), kind_status(K, Code).
persistence(S, P) :- kind(S, K), kind_persistence(K, P).

% ════════════════════════════════════════════════════════════════════
% Targets and operations
% A task acts on a target: an entity, or a connection (an aggregation).
% Compositions are reached through their child entity.
% ════════════════════════════════════════════════════════════════════

target(T, entity)      :- entity(T, _).
target(T, association) :- association(T, aggregation, _, _, _).

% offered_kind(Operation, Target, Kind, Reason): Kind can carry out Operation on Target.
% A kind that accepts moves (a transition) needs a target with a lifecycle.
offered_kind(Op, T, K, Reason) :-
    operation_kind(Op, K),
    target(T, Type),
    kind_target(K, Type),
    offered_for(K, T, Reason).

offered_for(K, T, has_lifecycle) :- kind_accepts(K, moves), !, lifecycle(T, _, _).
offered_for(_, _, target_type).

% The target a service acts on.
service_target(S, T) :- acts_on(S, T).
service_target(S, T) :- uses(S, T).
