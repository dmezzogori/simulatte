| Interpreter / backend | Mode | Hook | Total s | Jobs/s | RSS MiB | Build / run / extract / close ms |
|---|---|---|---:|---:|---:|---|
| CPython / simulatte | none | builtin | 3.2278 | 15,491 | 214.4 | 9.2 / 3103.4 / 2.4 / 112.8 |
| CPython / reference | none | builtin | 0.4372 | 114,377 | 123.2 | 7.3 / 417.0 / 1.3 / 11.5 |
| CPython / tuned | none | builtin | 0.3564 | 140,274 | 123.2 | 7.3 / 336.2 / 1.3 / 11.6 |
| CPython / native | none | builtin | 0.1331 | 375,759 | 122.8 | 91.6 / 20.0 / 15.3 / 6.1 |
| CPython / simulatte | none | python | 3.4251 | 14,598 | 217.9 | 9.0 / 3302.2 / 2.3 / 111.4 |
| CPython / reference | none | python | 0.5731 | 87,246 | 121.7 | 7.3 / 552.6 / 1.4 / 11.6 |
| CPython / tuned | none | python | 0.6090 | 82,101 | 123.2 | 7.3 / 588.6 / 1.4 / 11.6 |
| CPython / native | none | python | 0.4821 | 103,710 | 122.7 | 92.5 / 367.1 / 15.5 / 6.5 |
| CPython / simulatte | kpi | builtin | 3.2647 | 15,315 | 216.2 | 9.1 / 3141.0 / 2.4 / 112.0 |
| CPython / reference | kpi | builtin | 0.4613 | 108,391 | 123.5 | 7.4 / 441.1 / 1.3 / 11.5 |
| CPython / tuned | kpi | builtin | 0.3793 | 131,826 | 125.4 | 7.2 / 358.6 / 1.5 / 11.5 |
| CPython / native | kpi | builtin | 0.1351 | 370,063 | 122.8 | 92.6 / 20.4 / 15.8 / 6.2 |
| CPython / simulatte | kpi | python | 3.4900 | 14,327 | 216.0 | 9.1 / 3367.4 / 2.3 / 111.3 |
| CPython / reference | kpi | python | 0.6004 | 83,283 | 123.3 | 7.3 / 579.9 / 1.4 / 11.8 |
| CPython / tuned | kpi | python | 0.6278 | 79,647 | 123.2 | 7.4 / 607.7 / 1.3 / 11.5 |
| CPython / native | kpi | python | 0.4770 | 104,819 | 122.9 | 92.1 / 362.9 / 15.3 / 6.6 |
| CPython / simulatte | record | builtin | 3.4993 | 14,289 | 218.2 | 9.7 / 3370.1 / 2.3 / 116.9 |
| CPython / reference | record | builtin | 0.5703 | 87,671 | 124.7 | 7.8 / 549.3 / 1.4 / 11.8 |
| CPython / tuned | record | builtin | 0.4837 | 103,364 | 123.5 | 7.8 / 462.9 / 1.4 / 11.9 |
| CPython / native | record | builtin | 0.1417 | 352,946 | 122.7 | 93.3 / 26.3 / 15.4 / 6.7 |
| CPython / simulatte | record | python | 3.6634 | 13,649 | 218.4 | 9.7 / 3539.4 / 2.3 / 111.8 |
| CPython / reference | record | python | 0.7109 | 70,333 | 125.0 | 7.8 / 689.3 / 1.4 / 12.1 |
| CPython / tuned | record | python | 0.7405 | 67,518 | 123.5 | 7.9 / 719.6 / 1.4 / 11.8 |
| CPython / native | record | python | 0.4940 | 101,225 | 122.8 | 92.7 / 377.8 / 16.4 / 6.9 |
| PyPy / simulatte | none | builtin | 1.5598 | 32,055 | 319.0 | 13.8 / 1520.6 / 0.1 / 25.0 |
| PyPy / reference | none | builtin | 0.2947 | 169,646 | 163.4 | 10.6 / 270.9 / 3.3 / 9.8 |
| PyPy / tuned | none | builtin | 0.3231 | 154,753 | 163.2 | 10.9 / 298.5 / 3.3 / 10.4 |
| PyPy / native | none | builtin | 0.1182 | 423,179 | 173.3 | 63.9 / 20.3 / 27.3 / 6.8 |
| PyPy / simulatte | none | python | 1.5615 | 32,020 | 289.5 | 14.2 / 1521.9 / 0.1 / 25.4 |
| PyPy / reference | none | python | 0.3525 | 141,861 | 164.4 | 10.7 / 328.6 / 3.5 / 9.7 |
| PyPy / tuned | none | python | 0.3521 | 142,003 | 164.6 | 10.4 / 328.6 / 3.3 / 9.8 |
| PyPy / native | none | python | 0.4680 | 106,846 | 174.8 | 63.4 / 370.1 / 26.8 / 7.5 |
| PyPy / simulatte | kpi | builtin | 1.5752 | 31,742 | 284.4 | 14.1 / 1534.9 / 0.1 / 25.2 |
| PyPy / reference | kpi | builtin | 0.2964 | 168,686 | 163.9 | 10.5 / 272.6 / 3.3 / 9.8 |
| PyPy / tuned | kpi | builtin | 0.3258 | 153,447 | 164.6 | 10.9 / 301.1 / 3.4 / 10.5 |
| PyPy / native | kpi | builtin | 0.1189 | 420,524 | 173.5 | 63.8 / 20.5 / 27.4 / 7.1 |
| PyPy / simulatte | kpi | python | 1.5610 | 32,031 | 286.0 | 14.4 / 1521.5 / 0.1 / 25.5 |
| PyPy / reference | kpi | python | 0.3540 | 141,224 | 163.2 | 10.8 / 329.7 / 3.3 / 10.2 |
| PyPy / tuned | kpi | python | 0.3495 | 143,076 | 163.2 | 10.5 / 331.9 / 0.1 / 7.0 |
| PyPy / native | kpi | python | 0.4720 | 105,930 | 174.5 | 62.6 / 375.1 / 26.9 / 7.3 |
| PyPy / simulatte | record | builtin | 1.7404 | 28,729 | 306.8 | 10.4 / 1656.9 / 14.0 / 58.8 |
| PyPy / reference | record | builtin | 0.3482 | 143,580 | 188.5 | 11.4 / 322.9 / 6.4 / 7.6 |
| PyPy / tuned | record | builtin | 0.3788 | 131,990 | 175.6 | 11.4 / 360.5 / 0.1 / 6.9 |
| PyPy / native | record | builtin | 0.1242 | 402,650 | 173.3 | 63.7 / 25.7 / 27.4 / 7.4 |
| PyPy / simulatte | record | python | 1.6461 | 30,376 | 289.1 | 14.1 / 1606.8 / 0.1 / 25.1 |
| PyPy / reference | record | python | 0.4069 | 122,865 | 175.4 | 11.4 / 388.4 / 0.1 / 7.0 |
| PyPy / tuned | record | python | 0.4042 | 123,687 | 179.0 | 11.1 / 386.4 / 0.1 / 6.7 |
| PyPy / native | record | python | 0.4783 | 104,528 | 174.7 | 63.7 / 380.2 / 26.7 / 7.6 |

Native total throughput speedup ranges over equivalent current CPython Simulatte:
none builtin: 19.46–25.61x (median 21.62x)
none python: 6.98–9.20x (median 8.02x)
kpi builtin: 19.85–25.92x (median 21.79x)
kpi python: 7.07–9.38x (median 8.13x)
record builtin: 19.96–25.44x (median 22.18x)
record python: 7.31–9.74x (median 8.43x)
