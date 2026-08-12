* ---------------------------------------------------------------------------
* DEMO models - fictional, only here so the flow can be tried out end to end.
* Replace with the real model .inc / .lib paths inside the company network.
* ---------------------------------------------------------------------------

.subckt hbm_tx_drv VDDQ VSSQ DIN<0> DIN<1> DIN<2> DIN<3> PAD<0> PAD<1>
+ PAD<2> PAD<3> EN_TX ZQ_CODE<0> ZQ_CODE<1> ZQ_CODE<2> TM_DRV<0> TM_DRV<1>
+ SCAN_IN SCAN_OUT SCAN_EN
+ param: seg=4 rterm=34
Rd0 DIN<0> PAD<0> 'rterm'
Rd1 DIN<1> PAD<1> 'rterm'
Rd2 DIN<2> PAD<2> 'rterm'
Rd3 DIN<3> PAD<3> 'rterm'
Ren EN_TX VSSQ 1meg
Rz0 ZQ_CODE<0> VSSQ 1meg
Rz1 ZQ_CODE<1> VSSQ 1meg
Rz2 ZQ_CODE<2> VSSQ 1meg
Rt0 TM_DRV<0> VSSQ 1meg
Rt1 TM_DRV<1> VSSQ 1meg
Rs0 SCAN_IN VSSQ 1meg
Rs1 SCAN_OUT VSSQ 1meg
Rs2 SCAN_EN VSSQ 1meg
Rv VDDQ VSSQ 1meg
.ends hbm_tx_drv

.subckt hbm_rx_amp VDDQ VSSQ PAD<0> PAD<1> PAD<2> PAD<3> DOUT<0> DOUT<1>
+ DOUT<2> DOUT<3> VREF CLK_R CLKB_R EN_RX OFFSET<0> OFFSET<1> OFFSET<2>
+ OFFSET<3> TM_RX<0> TM_RX<1> SPARE0 SPARE1
+ param: cload=5f
R0 PAD<0> DOUT<0> 1k
R1 PAD<1> DOUT<1> 1k
R2 PAD<2> DOUT<2> 1k
R3 PAD<3> DOUT<3> 1k
Rv VREF VSSQ 1meg
Rc CLK_R CLKB_R 1meg
Re EN_RX VSSQ 1meg
Ro0 OFFSET<0> VSSQ 1meg
Ro1 OFFSET<1> VSSQ 1meg
Ro2 OFFSET<2> VSSQ 1meg
Ro3 OFFSET<3> VSSQ 1meg
Rt0 TM_RX<0> VSSQ 1meg
Rt1 TM_RX<1> VSSQ 1meg
Rp0 SPARE0 VSSQ 1meg
Rp1 SPARE1 VSSQ 1meg
Cl DOUT<0> VSSQ 'cload'
.ends hbm_rx_amp

.subckt hbm_pad_esd VDDQ VSSQ PAD BUMP
Resd PAD BUMP 0.5
Cesd BUMP VSSQ 100f
Rv VDDQ VSSQ 1meg
.ends hbm_pad_esd

.subckt hbm_vref_gen VDDQ VSSQ VREF TRIM<0> TRIM<1> TRIM<2> EN_VREF
R0 VDDQ VREF 10k
R1 VREF VSSQ 10k
Rt0 TRIM<0> VSSQ 1meg
Rt1 TRIM<1> VSSQ 1meg
Rt2 TRIM<2> VSSQ 1meg
Re EN_VREF VSSQ 1meg
.ends hbm_vref_gen

.subckt hbm_channel IN<0> IN<1> IN<2> IN<3> OUT<0> OUT<1> OUT<2> OUT<3> VSSQ
+ param: zo=50 td=30p
T0 IN<0> VSSQ OUT<0> VSSQ Z0='zo' TD='td'
T1 IN<1> VSSQ OUT<1> VSSQ Z0='zo' TD='td'
T2 IN<2> VSSQ OUT<2> VSSQ Z0='zo' TD='td'
T3 IN<3> VSSQ OUT<3> VSSQ Z0='zo' TD='td'
.ends hbm_channel
