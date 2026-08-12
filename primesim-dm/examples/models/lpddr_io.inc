* ---------------------------------------------------------------------------
* DEMO models - fictional.  Shows the bus-declaration form ``DQ[7:0]``, which
* `expand_buses: true` turns into DQ[7] ... DQ[0] before the rules run.
* ---------------------------------------------------------------------------

.subckt lpddr_dq_io VDD2 VDDQ VSS DQ[7:0] DQS_T DQS_C DMI TX_DATA[7:0]
+ RX_DATA[7:0] ODT_EN[2:0] DRV_STR[2:0] VREF_DQ TM_MODE[1:0] MRW_EN
+ param: odt=40 drv=34
Rq0 DQ[0] TX_DATA[0] 'drv'
Rq1 DQ[1] TX_DATA[1] 'drv'
Rq2 DQ[2] TX_DATA[2] 'drv'
Rq3 DQ[3] TX_DATA[3] 'drv'
Rq4 DQ[4] TX_DATA[4] 'drv'
Rq5 DQ[5] TX_DATA[5] 'drv'
Rq6 DQ[6] TX_DATA[6] 'drv'
Rq7 DQ[7] TX_DATA[7] 'drv'
Rr0 DQ[0] RX_DATA[0] 1k
Rr1 DQ[1] RX_DATA[1] 1k
Rr2 DQ[2] RX_DATA[2] 1k
Rr3 DQ[3] RX_DATA[3] 1k
Rr4 DQ[4] RX_DATA[4] 1k
Rr5 DQ[5] RX_DATA[5] 1k
Rr6 DQ[6] RX_DATA[6] 1k
Rr7 DQ[7] RX_DATA[7] 1k
Rs DQS_T DQS_C 1meg
Rm DMI VSS 1meg
Ro0 ODT_EN[0] VSS 1meg
Ro1 ODT_EN[1] VSS 1meg
Ro2 ODT_EN[2] VSS 1meg
Rd0 DRV_STR[0] VSS 1meg
Rd1 DRV_STR[1] VSS 1meg
Rd2 DRV_STR[2] VSS 1meg
Rv VREF_DQ VSS 1meg
Rt0 TM_MODE[0] VSS 1meg
Rt1 TM_MODE[1] VSS 1meg
Rw MRW_EN VSS 1meg
Rp VDD2 VDDQ 1meg
Rg VDDQ VSS 1meg
.ends lpddr_dq_io

.subckt lpddr_pkg_ch IN[7:0] OUT[7:0] VSS
+ param: zo=45 td=25p
T0 IN[0] VSS OUT[0] VSS Z0='zo' TD='td'
T1 IN[1] VSS OUT[1] VSS Z0='zo' TD='td'
T2 IN[2] VSS OUT[2] VSS Z0='zo' TD='td'
T3 IN[3] VSS OUT[3] VSS Z0='zo' TD='td'
T4 IN[4] VSS OUT[4] VSS Z0='zo' TD='td'
T5 IN[5] VSS OUT[5] VSS Z0='zo' TD='td'
T6 IN[6] VSS OUT[6] VSS Z0='zo' TD='td'
T7 IN[7] VSS OUT[7] VSS Z0='zo' TD='td'
.ends lpddr_pkg_ch
