// CHECKSUM_VECTOR_INCLUDE_FORMAT 1
// CANONICAL_TEXT_SHA256 b3b2117b069afb3e0da24dfc6c822ac125c8a755e0853d4fbf1ad3af81b30364
// Generated from checksum_vectors.txt; do not edit by hand.
task automatic run_checksum_vectors_v1;
    begin
        vector_reset16();
        vector_cycle16("even_length", 0, 1, 1, 64'h0000000078563412, 8'h0f, 1, 1, 16'h0000, 0, 1, 1, 1, 3'd0, 16'h9753, 16'h68ac, 16'd4);
        vector_reset16();
        vector_cycle16("odd_length", 0, 1, 1, 64'h0000000000563412, 8'h07, 1, 1, 16'h0000, 0, 1, 1, 1, 3'd0, 16'h97cb, 16'h6834, 16'd3);
        vector_reset16();
        vector_cycle16("poisoned_partial_even", 0, 1, 1, 64'hddccbbaa78563412, 8'h0f, 1, 1, 16'h0000, 0, 1, 1, 1, 3'd0, 16'h9753, 16'h68ac, 16'd4);
        vector_reset16();
        vector_cycle16("poisoned_partial_odd", 0, 1, 1, 64'he5d4c3b2a1563412, 8'h07, 1, 1, 16'h0000, 0, 1, 1, 1, 3'd0, 16'h97cb, 16'h6834, 16'd3);
        vector_reset16();
        vector_cycle16("carry_folding", 0, 1, 1, 64'h00000000ffffffff, 8'h0f, 1, 1, 16'h0000, 0, 1, 1, 1, 3'd0, 16'h0000, 16'hffff, 16'd4);
        vector_reset16();
        vector_cycle16("seeded_multibeat", 0, 1, 1, 64'h0807060504030201, 8'hff, 1, 0, 16'h1234, 0, 1, 1, 0, 3'd0, 16'h0000, 16'h0000, 16'd0);
        vector_cycle16("seeded_multibeat", 1, 1, 1, 64'h0000000d0c0b0a09, 8'h1f, 0, 1, 16'h0000, 0, 1, 1, 1, 3'd0, 16'hbca1, 16'h435e, 16'd13);
        vector_reset16();
        vector_cycle16("missing_first", 0, 1, 1, 64'h0000000000000012, 8'h01, 0, 1, 16'h0000, 0, 1, 1, 1, 3'd1, 16'h0000, 16'h0000, 16'd0);
        vector_reset16();
        vector_cycle16("unexpected_first", 0, 1, 1, 64'h0706050403020100, 8'hff, 1, 0, 16'h0000, 0, 1, 1, 0, 3'd0, 16'h0000, 16'h0000, 16'd0);
        vector_cycle16("unexpected_first", 1, 1, 1, 64'h0000000000000008, 8'h01, 1, 1, 16'h0000, 0, 1, 1, 1, 3'd2, 16'h0000, 16'h0000, 16'd8);
        vector_reset16();
        vector_cycle16("invalid_nonfinal_keep", 0, 1, 1, 64'h0000000000000000, 8'h7f, 1, 0, 16'h0000, 0, 1, 1, 1, 3'd3, 16'h0000, 16'h0000, 16'd0);
        vector_reset16();
        vector_cycle16("sparse_final_keep", 0, 1, 1, 64'h0000000000000000, 8'h55, 1, 1, 16'h0000, 0, 1, 1, 1, 3'd3, 16'h0000, 16'h0000, 16'd0);
        vector_reset16();
        vector_cycle16("empty_final", 0, 1, 1, 64'h0000000000000000, 8'h00, 1, 1, 16'h0000, 0, 1, 1, 1, 3'd4, 16'h0000, 16'h0000, 16'd0);
        vector_reset4();
        vector_cycle4("length_boundary_success", 0, 1, 1, 64'h0706050403020100, 8'hff, 1, 0, 16'h0000, 0, 1, 1, 0, 3'd0, 16'h0000, 16'h0000, 4'd0);
        vector_cycle4("length_boundary_success", 1, 1, 1, 64'ha50e0d0c0b0a0908, 8'h7f, 0, 1, 16'h0000, 0, 1, 1, 1, 3'd0, 16'hc7ce, 16'h3831, 4'd15);
        vector_reset4();
        vector_cycle4("length_boundary_overflow", 0, 1, 1, 64'h0706050403020100, 8'hff, 1, 0, 16'h0000, 0, 1, 1, 0, 3'd0, 16'h0000, 16'h0000, 4'd0);
        vector_cycle4("length_boundary_overflow", 1, 1, 1, 64'h0f0e0d0c0b0a0908, 8'hff, 0, 1, 16'h0000, 0, 1, 1, 1, 3'd5, 16'h0000, 16'h0000, 4'd8);
        vector_reset16();
        vector_cycle16("reset_recovery", 0, 1, 1, 64'h0706050403020100, 8'hff, 1, 0, 16'h0000, 0, 1, 1, 0, 3'd0, 16'h0000, 16'h0000, 16'd0);
        vector_cycle16("reset_recovery", 1, 0, 0, 64'h0000000000000000, 8'h00, 0, 0, 16'h0000, 0, 1, 0, 0, 3'd0, 16'h0000, 16'h0000, 16'd0);
        vector_cycle16("reset_recovery", 2, 1, 1, 64'h000000000001feca, 8'h07, 1, 1, 16'h0000, 0, 1, 1, 1, 3'd0, 16'h3401, 16'hcbfe, 16'd3);
        vector_reset16();
        vector_cycle16("stall_and_zero_bubble", 0, 1, 1, 64'h0000000000003412, 8'h03, 1, 1, 16'h0000, 0, 1, 1, 1, 3'd0, 16'hedcb, 16'h1234, 16'd2);
        vector_cycle16("stall_and_zero_bubble", 1, 1, 0, 64'h0000000000000000, 8'h00, 0, 0, 16'h0000, 0, 0, 0, 1, 3'd0, 16'hedcb, 16'h1234, 16'd2);
        vector_cycle16("stall_and_zero_bubble", 2, 1, 0, 64'h0000000000000000, 8'h00, 0, 0, 16'h0000, 0, 0, 0, 1, 3'd0, 16'hedcb, 16'h1234, 16'd2);
        vector_cycle16("stall_and_zero_bubble", 3, 1, 1, 64'h00000000009a7856, 8'h07, 1, 1, 16'h0007, 1, 1, 1, 1, 3'd0, 16'h0f80, 16'hf07f, 16'd3);
        vector_cycle16("stall_and_zero_bubble", 4, 1, 0, 64'h0000000000000000, 8'h00, 0, 0, 16'h0000, 1, 1, 0, 0, 3'd0, 16'h0000, 16'h0000, 16'd0);
    end
endtask
