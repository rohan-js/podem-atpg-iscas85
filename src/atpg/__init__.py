"""PODEM ATPG + parallel-pattern fault simulation on the ISCAS-85 benchmarks.

Pure-stdlib Python.  Modules:
  bench       -- '.bench' netlist parser / circuit graph
  simulate    -- bit-parallel (parallel-pattern) good-machine + fault sim
  scalar_sim  -- naive independent reference simulator (cross-validation)
  faults      -- fault-site generation (with explicit fanout branches),
                 equivalence + dominance fault collapsing
  podem       -- PODEM stuck-at ATPG (5-valued logic, event-driven implication)
  report      -- summary.json / summary.md writers
"""
