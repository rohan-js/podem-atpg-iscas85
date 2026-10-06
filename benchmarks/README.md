# ISCAS-85 combinational benchmark circuits

Standard `.bench`-format netlists of the ISCAS-85 combinational
benchmark set: `c17`, `c432`, `c499`, `c880`, `c1355`, `c1908`,
`c2670`, `c3540`, `c5315`, `c6288`, `c7552`.

- **Source:** <https://www.pld.ttu.ee/~maksim/benchmarks/iscas85/bench/>
  (Tallinn University of Technology mirror of the MCNC benchmark
  distribution), retrieved 2026-10-06.
- **Provenance:** F. Brglez and H. Fujiwara, "A Neutral Netlist of 10
  Combinational Benchmark Circuits and a Target Translator in Fortran,"
  Proc. IEEE Int. Symposium on Circuits and Systems (ISCAS), 1985;
  gate counts as published in F. Brglez, D. Bryan, K. Kozminski,
  "Combinational Profiles of Sequential Benchmark Circuits," ISCAS 1989.
- **Status:** These benchmarks have been freely redistributed for
  research and education for decades.  Files are unmodified.

The test suite (`tests/test_parser.py`) asserts the PI/PO/gate counts of
every file against the published ISCAS-85 statistics table.
