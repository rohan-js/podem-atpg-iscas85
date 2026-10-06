# ISCAS-85 Stuck-At ATPG Results (PODEM + parallel-pattern fault simulation)

Weighted collapsed-fault coverage: **98.08%** (25739/26243 collapsed faults) | full-list coverage: **98.21%** (59823/60912)

| circuit | PI | PO | gates | faults (full) | faults (collapsed) | detected | redundant | aborted | coverage (collapsed) | coverage (full) | patterns | ATPG s |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| c17 | 5 | 2 | 6 | 34 | 16 | 16 | 0 | 0 | 100.0% | 100.0% | 5 | 0.003 |
| c432 | 36 | 7 | 160 | 864 | 449 | 444 | 0 | 5 | 98.89% | 97.69% | 55 | 125.484 |
| c499 | 41 | 32 | 202 | 998 | 706 | 698 | 0 | 8 | 98.87% | 99.2% | 54 | 126.795 |
| c880 | 60 | 26 | 383 | 1760 | 745 | 745 | 0 | 0 | 100.0% | 100.0% | 54 | 3.373 |
| c1355 | 41 | 32 | 546 | 2710 | 1210 | 1202 | 0 | 8 | 99.34% | 99.52% | 86 | 605.764 |
| c1908 | 33 | 25 | 880 | 3816 | 1566 | 1557 | 6 | 3 | 99.43% | 99.63% | 123 | 175.214 |
| c2670 | 233 | 140 | 1193 | 5340 | 2317 | 2204 | 89 | 24 | 95.12% | 96.01% | 111 | 526.53 |
| c3540 | 50 | 22 | 1669 | 7080 | 2786 | 2656 | 87 | 43 | 95.33% | 95.38% | 159 | 1642.487 |
| c5315 | 178 | 123 | 2307 | 10630 | 4492 | 4433 | 56 | 3 | 98.69% | 99.13% | 121 | 107.169 |
| c6288 | 32 | 32 | 2416 | 12576 | 5824 | 5790 | 32 | 2 | 99.42% | 98.82% | 40 | 328.27 |
| c7552 | 207 | 108 | 3512 | 15104 | 6132 | 5994 | 62 | 76 | 97.75% | 98.32% | 247 | 1692.513 |

Fault model: stuck-at on every net of the branch-expanded circuit
(stems and fanout branches are separate sites).  Collapsed via
equivalence + dominance; full-list coverage derived through the
collapse representative mapping validated in the test suite.
Redundant = PODEM proved untestable.  Aborted = backtrack limit hit
on the deterministic pass and on all randomized-restart attempts.
