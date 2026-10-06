% ════════════════════════════════════════════════════════════════════
% Core decisions (ships with the harness)
% What follows directly from a service's kind.
% ════════════════════════════════════════════════════════════════════

method(S, M)      :- kind(S, K), kind_method(K, M).
status(S, Code)   :- kind(S, K), kind_status(K, Code).
persistence(S, P) :- kind(S, K), kind_persistence(K, P).
