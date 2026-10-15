% ════════════════════════════════════════════════════════════════════
% CRUD API factory: API anatomy and delete rules
%
% How each service's API is shaped: path, parameters, body, response,
% errors, and what a delete does to dependents. Choices (plural paths,
% param names, nesting, paging names, status codes) are facts compiled
% from technical.yaml › api, so a project can override them; these rules
% are only the mechanics. Every decision carries the id of its rule.
% ════════════════════════════════════════════════════════════════════

:- discontiguous api_path/3, request_field/3, query_param/4, api_error/5, response/3.

% ── The entity a service is about, and its parent ───────────────────

service_entity(S, E) :- acts_on(S, E).

% A composition child is addressed under its parent (when nesting is on).
service_parent(S, P) :-
    nest_children(true), acts_on(S, E), association(_, composition, P, E, _).

% ── Paths ───────────────────────────────────────────────────────────

collection_path(S, Path) :-
    service_parent(S, P), !,
    resource(P, PS), id_param(P, PI), service_entity(S, E), resource(E, ES),
    format(atom(Path), '/~w/{~w}/~w', [PS, PI, ES]).
collection_path(S, Path) :-
    service_entity(S, E), resource(E, ES),
    format(atom(Path), '/~w', [ES]).

item_path(S, Path) :-
    collection_path(S, C), service_entity(S, E), id_param(E, I),
    format(atom(Path), '~w/{~w}', [C, I]).

% archive_book -> archive: the service is named {verb}_{entity}.
transition_verb(S, Verb) :-
    acts_on(S, E), atom_concat('_', E, Suffix), atom_concat(Verb, Suffix, S), Verb \== ''.

% Call with S bound: negation (\+) never binds a variable.
overridden(S) :- service_path(S, _).

api_path(S, Path, 'service-override') :- service_path(S, Path).
api_path(S, Path, 'collection-path') :-
    kind(S, K), memberchk(K, [create, query]), \+ overridden(S), collection_path(S, Path).
api_path(S, Path, 'item-path') :-
    kind(S, K), memberchk(K, [read_one, change, replace, delete]), \+ overridden(S),
    item_path(S, Path).
api_path(S, Path, 'transition-verb-in-path') :-
    kind(S, transition), \+ overridden(S), transition_style(verb_in_path),
    item_path(S, I), transition_verb(S, V), format(atom(Path), '~w/~w', [I, V]).
api_path(S, Path, 'transition-field-in-body') :-
    kind(S, transition), \+ overridden(S), transition_style(field_in_body), item_path(S, Path).
% many-to-many: /collections/{collection_id}/books/{book_id}
api_path(S, Path, 'link-path') :-
    kind(S, K), memberchk(K, [link, unlink]), \+ overridden(S),
    uses(S, A), association(A, _, P, C, many_to_many),
    resource(P, PS), id_param(P, PI), resource(C, CS), id_param(C, CI),
    format(atom(Path), '/~w/{~w}/~w/{~w}', [PS, PI, CS, CI]).
% one-to-many: /books/{book_id}/publisher
api_path(S, Path, 'reference-path') :-
    kind(S, K), memberchk(K, [link, unlink]), \+ overridden(S),
    uses(S, A), association(A, _, P, C, one_to_many),
    resource(C, CS), id_param(C, CI), singular(P, PN),
    format(atom(Path), '/~w/{~w}/~w', [CS, CI, PN]).

% Every {param} in the path names the entity it identifies.
path_param(S, Name, Entity, 'identifier-in-path') :-
    api_path(S, Path, _), id_param(Entity, Name),
    format(atom(Token), '{~w}', [Name]), sub_atom(Path, _, _, _, Token).

% ── Request body ────────────────────────────────────────────────────

editable(E, A) :- attribute(E, A, _), \+ generated(E, A).

request_field(S, A, 'create-body') :- kind(S, create), service_entity(S, E), editable(E, A).
request_field(S, A, 'replace-body') :- kind(S, replace), service_entity(S, E), editable(E, A).
request_field(S, A, 'change-body') :- kind(S, change), changes(S, A).
request_field(S, F, 'transition-field-in-body') :-
    kind(S, transition), transition_style(field_in_body), acts_on(S, E), lifecycle(E, F, _).
request_field(S, F, 'reference-link-body') :-
    kind(S, link), uses(S, A), association(A, _, P, _, one_to_many), id_param(P, F).

% ── Query parameters: only on lists ─────────────────────────────────

query_param(S, A, filter, 'filters-are-params') :- kind(S, query), filter(S, A).
% Paging follows the pagination component, so removing that rule removes paging.
query_param(S, N, paging, R) :-
    kind(S, query), component(S, pagination, R), paging_param(_, N).

% ── Response ────────────────────────────────────────────────────────

response(S, item, 'item-response') :-
    kind(S, K), memberchk(K, [create, read_one, change, replace, transition]).
response(S, list, 'list-response') :- kind(S, query).
response(S, none, 'no-content') :- kind(S, K), memberchk(K, [delete, link, unlink]).

% An item shows every attribute and its stage; a list shows each item's summary.
response_field(S, A, 'item-response') :-
    response(S, item, _), service_entity(S, E),
    ( attribute(E, A, _) ; lifecycle(E, A, _) ).
response_field(S, A, 'list-summary') :-
    response(S, list, _), service_entity(S, E), summary_field(E, A).

% ── Errors ──────────────────────────────────────────────────────────

% api_error(Service, Status, What, Subject, Rule)
api_error(S, Code, unknown, X, 'not-found') :-
    error_code(not_found, Code), path_param(S, _, X, _).
api_error(S, Code, invalid_body, none, 'invalid-body') :-
    error_code(invalid, Code), once(request_field(S, _, _)).
api_error(S, Code, unique_taken, A, 'unique-taken') :-
    error_code(conflict, Code), request_field(S, A, _), service_entity(S, E), unique(E, A).
api_error(S, Code, move_not_allowed, none, 'transition-guard') :-
    error_code(conflict, Code), kind(S, transition).
api_error(S, Code, still_referenced, D, 'delete-restricted') :-
    error_code(conflict, Code), kind(S, delete), acts_on(S, E),
    delete_effect(E, D, restrict, _).

% ── Dependents and delete ───────────────────────────────────────────

% dependent(Entity, Dependent, How)
dependent(E, D, cascade) :- association(_, composition, E, D, _).
dependent(E, D, Policy) :- association(A, aggregation, E, D, one_to_many), on_delete(A, Policy).
dependent(E, A, remove_links) :- association(A, aggregation, E, _, many_to_many).
dependent(E, A, remove_links) :- association(A, aggregation, _, E, many_to_many).

% Compositions cascade all the way down (tabled: always ends).
:- table cascades/2.
cascades(E, D) :- dependent(E, D, cascade).
cascades(E, D) :- dependent(E, X, cascade), cascades(X, D).

% What goes when E is deleted: E itself and everything that cascades from it.
deleted_with(E, E).
deleted_with(E, D) :- cascades(E, D).

% delete_effect(Entity, Dependent, Effect, Rule)
delete_effect(E, D, cascade, 'composition-cascade') :- cascades(E, D).
delete_effect(E, D, restrict, 'reference-restrict') :- deleted_with(E, X), dependent(X, D, restrict).
delete_effect(E, D, set_null, 'reference-set-null') :- deleted_with(E, X), dependent(X, D, set_null).
delete_effect(E, A, remove_links, 'link-rows-removed') :-
    deleted_with(E, X), dependent(X, A, remove_links).
