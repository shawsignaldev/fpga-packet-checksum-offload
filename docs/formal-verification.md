# Formal Verification

## Bounded evidence

`formal/checksum16_stream.sby` defines four tasks. The `positive` task performs bounded model checking to depth 20. The `cover` task searches to depth 20. This is bounded evidence, not an unbounded proof and not physical timing closure.

The harness binds to DUT packet and response state. Assertions check reset clearing, accepted-transfer state transitions, state stability without an accepted request, response stability under backpressure, framing status, length accounting, and valid result ranges.

## Reachability

Ten cover statements require witnesses for:

1. successful completion;
2. missing-first status;
3. unexpected-first status;
4. invalid-keep status;
5. empty-final status;
6. length-overflow status;
7. multibeat completion;
8. a stalled response;
9. zero-bubble response replacement;
10. recurrent reset after observed activity.

## Negative controls

Two bounded mutations demonstrate that the property set rejects targeted defects. `odd_padding_mutation` drops the zero pad for an odd final byte. `stall_state_mutation` changes packet state while a request is not accepted. Each task must fail only its named control assertion and produce a fresh nonempty counterexample trace.

## Reproduction

```bash
sby -f formal/checksum16_stream.sby positive
sby -f formal/checksum16_stream.sby cover
sby -f formal/checksum16_stream.sby odd_padding_mutation
sby -f formal/checksum16_stream.sby stall_state_mutation
```

The first two commands must pass; the two intentional mutation commands must fail with their expected markers. The pytest harness removes stale work directories and validates fresh status, log, and trace artifacts. Verilator, Icarus Verilog, Yosys, and SymbiYosys are all exercised by `tests/test_rtl_contract.py`.
