`default_nettype none

module checksum16_stream_formal #(
    parameter bit FAULT_DROP_ODD_PAD = 1'b0,
    parameter bit FAULT_MUTATE_STALL = 1'b0
);
    localparam [2:0] STATUS_SUCCESS          = 3'd0;
    localparam [2:0] STATUS_MISSING_FIRST    = 3'd1;
    localparam [2:0] STATUS_UNEXPECTED_FIRST = 3'd2;
    localparam [2:0] STATUS_INVALID_KEEP     = 3'd3;
    localparam [2:0] STATUS_EMPTY_FINAL      = 3'd4;
    localparam [2:0] STATUS_LENGTH_OVERFLOW  = 3'd5;

    (* gclk *) reg clk;
    (* anyseq *) reg reset_n;
    (* anyseq *) reg request_valid;
    wire request_ready;
    (* anyseq *) reg [63:0] request_data;
    (* anyseq *) reg [7:0] request_keep;
    (* anyseq *) reg request_first;
    (* anyseq *) reg request_last;
    (* anyseq *) reg [15:0] request_seed;
    (* anyseq *) reg response_ready;
    wire response_valid;
    wire [2:0] response_status;
    wire [15:0] response_checksum;
    wire [15:0] response_folded_sum;
    wire [3:0] response_byte_length;
    wire dut_packet_active;
    wire [15:0] dut_packet_sum;
    wire [3:0] dut_packet_byte_length;
    wire [15:0] dut_folded_combined_sum;

    wire request_fire = reset_n && request_valid && request_ready;
    reg past_valid = 1'b0;
    reg shadow_active = 1'b0;
    reg [3:0] shadow_length = 4'd0;
    reg shadow_saw_nonfinal = 1'b0;
    reg activity_seen = 1'b0;

    reg keep_legal;
    reg [3:0] accepted_byte_count;
    reg [2:0] expected_status;
    reg [4:0] prospective_length;
    integer keep_index;

    checksum16_stream #(
        .DATA_WIDTH(64),
        .KEEP_WIDTH(8),
        .LENGTH_WIDTH(4),
        .FAULT_DROP_ODD_PAD(FAULT_DROP_ODD_PAD),
        .FAULT_MUTATE_STALL(FAULT_MUTATE_STALL)
    ) dut (
        .clk(clk),
        .reset_n(reset_n),
        .request_valid(request_valid),
        .request_ready(request_ready),
        .request_data(request_data),
        .request_keep(request_keep),
        .request_first(request_first),
        .request_last(request_last),
        .request_seed(request_seed),
        .response_ready(response_ready),
        .response_valid(response_valid),
        .response_status(response_status),
        .response_checksum(response_checksum),
        .response_folded_sum(response_folded_sum),
        .response_byte_length(response_byte_length),
        .formal_packet_active(dut_packet_active),
        .formal_packet_sum(dut_packet_sum),
        .formal_packet_byte_length(dut_packet_byte_length),
        .formal_folded_combined_sum(dut_folded_combined_sum)
    );

    function automatic [15:0] fold_one_byte(input [19:0] value);
        reg [20:0] first_fold;
        reg [20:0] second_fold;
        begin
            first_fold = {5'd0, value[15:0]} + {17'd0, value[19:16]};
            second_fold = {5'd0, first_fold[15:0]}
                        + {16'd0, first_fold[20:16]};
            fold_one_byte = second_fold[15:0]
                          + {11'd0, second_fold[20:16]};
        end
    endfunction

    wire [19:0] odd_input_sum = {4'd0, request_seed}
                                 + {4'd0, request_data[7:0], 8'h00};
    wire [15:0] odd_expected_sum = fold_one_byte(odd_input_sum);

    always @* begin
        keep_legal = 1'b1;
        accepted_byte_count = 4'd0;
        keep_index = 0;
        if (!request_last) begin
            if (request_keep != 8'hff) begin
                keep_legal = 1'b0;
            end else begin
                accepted_byte_count = 4'd8;
            end
        end else if (request_keep == 8'h00) begin
            keep_legal = 1'b0;
        end else if ((request_keep & (request_keep + 1'b1)) != 8'h00) begin
            keep_legal = 1'b0;
        end else begin
            for (keep_index = 0; keep_index < 8; keep_index = keep_index + 1) begin
                accepted_byte_count = accepted_byte_count + request_keep[keep_index];
            end
        end
    end

    always @* begin
        prospective_length = {1'b0, shadow_length} + accepted_byte_count;
        if (!shadow_active && !request_first) begin
            expected_status = STATUS_MISSING_FIRST;
        end else if (shadow_active && request_first) begin
            expected_status = STATUS_UNEXPECTED_FIRST;
        end else if (request_last && request_keep == 8'h00) begin
            expected_status = STATUS_EMPTY_FINAL;
        end else if (!keep_legal) begin
            expected_status = STATUS_INVALID_KEEP;
        end else if (prospective_length > 5'd15) begin
            expected_status = STATUS_LENGTH_OVERFLOW;
        end else begin
            expected_status = STATUS_SUCCESS;
        end
    end

    // This shadow records only accepted framing and length. It is not a
    // checksum oracle; arithmetic properties are checked from DUT results.
    always @(posedge clk) begin
        past_valid <= 1'b1;
        if (!past_valid) begin
            assume(!reset_n);
        end

        if (past_valid && reset_n && request_fire) begin
            activity_seen <= 1'b1;
        end

        if (!reset_n) begin
            shadow_active <= 1'b0;
            shadow_length <= 4'd0;
            shadow_saw_nonfinal <= 1'b0;
        end else if (request_fire) begin
            if (expected_status != STATUS_SUCCESS || request_last) begin
                shadow_active <= 1'b0;
                shadow_length <= 4'd0;
                shadow_saw_nonfinal <= 1'b0;
            end else begin
                shadow_active <= 1'b1;
                shadow_length <= prospective_length[3:0];
                shadow_saw_nonfinal <= 1'b1;
            end
        end
    end

`ifndef FORMAL_ODD_CONTROL
`ifndef FORMAL_STALL_CONTROL
    always @(posedge clk) begin
        if (past_valid) begin
            ASSERT_DUT_SHADOW_EQUIVALENCE:
                assert(dut_packet_active == shadow_active
                       && dut_packet_byte_length == shadow_length);

            ASSERT_REQUEST_READY:
                assert(request_ready == (!response_valid || response_ready));

            if ($past(reset_n && response_valid && !response_ready)) begin
                ASSERT_STALL_RESPONSE:
                    assert(response_valid
                           && response_status == $past(response_status)
                           && response_checksum == $past(response_checksum)
                           && response_folded_sum == $past(response_folded_sum)
                           && response_byte_length == $past(response_byte_length));
            end

            if ($past(reset_n && !request_fire)) begin
                ASSERT_ACCEPTED_STATE_STABLE:
                    assert(shadow_active == $past(shadow_active)
                           && shadow_length == $past(shadow_length));
                ASSERT_DUT_STATE_STABLE:
                    assert(dut_packet_active == $past(dut_packet_active)
                           && dut_packet_sum == $past(dut_packet_sum)
                           && dut_packet_byte_length
                              == $past(dut_packet_byte_length));
            end

            if ($past(!reset_n)) begin
                ASSERT_RESET_CLEARS:
                    assert(!response_valid
                           && response_status == STATUS_SUCCESS
                           && response_checksum == 16'd0
                           && response_folded_sum == 16'd0
                           && response_byte_length == 4'd0
                           && !shadow_active
                           && shadow_length == 4'd0);
                ASSERT_DUT_RESET_CLEARS:
                    assert(!dut_packet_active
                           && dut_packet_sum == 16'd0
                           && dut_packet_byte_length == 4'd0);
            end

            if (!dut_packet_active) begin
                ASSERT_DUT_INACTIVE_NORMALIZED:
                    assert(dut_packet_sum == 16'd0
                           && dut_packet_byte_length == 4'd0);
            end

            if (response_valid) begin
                ASSERT_STATUS_RANGE:
                    assert(response_status <= STATUS_LENGTH_OVERFLOW);
                ASSERT_SUCCESS_COMPLEMENT:
                    assert(response_status != STATUS_SUCCESS
                           || response_checksum == ~response_folded_sum);
                ASSERT_ERROR_ZERO_FIELDS:
                    assert(response_status == STATUS_SUCCESS
                           || (response_checksum == 16'd0
                               && response_folded_sum == 16'd0));
                ASSERT_RESULT_LENGTH:
                    assert(response_byte_length <= 4'd15
                           && (response_status != STATUS_SUCCESS
                               || response_byte_length != 4'd0));
            end

            if ($past(request_fire)) begin
                if ($past(expected_status) != STATUS_SUCCESS) begin
                    ASSERT_ERROR_TERMINATES:
                        assert(response_valid
                               && response_status == $past(expected_status)
                               && response_checksum == 16'd0
                               && response_folded_sum == 16'd0
                               && response_byte_length == $past(shadow_length)
                               && !shadow_active);
                    ASSERT_DUT_ERROR_CLEARS:
                        assert(!dut_packet_active
                               && dut_packet_sum == 16'd0
                               && dut_packet_byte_length == 4'd0);
                end else if ($past(request_last)) begin
                    ASSERT_ACCEPTED_TRANSITION:
                        assert(response_valid
                               && response_status == STATUS_SUCCESS
                               && response_byte_length
                                  == $past(prospective_length[3:0])
                               && !shadow_active);
                    ASSERT_DUT_SUCCESS_CLEARS:
                        assert(!dut_packet_active
                               && dut_packet_sum == 16'd0
                               && dut_packet_byte_length == 4'd0);
                end else begin
                    ASSERT_ACCEPTED_NONFINAL_TRANSITION:
                        assert(!response_valid
                               && shadow_active
                               && shadow_length
                                  == $past(prospective_length[3:0]));
                    ASSERT_DUT_NONFINAL_TRANSITION:
                        assert(dut_packet_active
                               && dut_packet_sum
                                  == $past(dut_folded_combined_sum)
                               && dut_packet_byte_length
                                  == $past(prospective_length[3:0]));
                end
            end

            if ($past(response_valid
                      && response_status != STATUS_SUCCESS
                      && response_ready
                      && request_fire
                      && expected_status == STATUS_SUCCESS
                      && request_last)) begin
                ASSERT_ERROR_RECOVERY:
                    assert(response_valid && response_status == STATUS_SUCCESS);
            end

            if ($past(request_fire
                      && (expected_status != STATUS_SUCCESS || request_last))) begin
                ASSERT_PACKET_STATE_IDLE:
                    assert(!shadow_active && shadow_length == 4'd0);
            end

            COVER_SUCCESS:
                cover(response_valid && response_status == STATUS_SUCCESS);
            COVER_MISSING_FIRST:
                cover(response_valid && response_status == STATUS_MISSING_FIRST);
            COVER_UNEXPECTED_FIRST:
                cover(response_valid && response_status == STATUS_UNEXPECTED_FIRST);
            COVER_INVALID_KEEP:
                cover(response_valid && response_status == STATUS_INVALID_KEEP);
            COVER_EMPTY_FINAL:
                cover(response_valid && response_status == STATUS_EMPTY_FINAL);
            COVER_LENGTH_OVERFLOW:
                cover(response_valid && response_status == STATUS_LENGTH_OVERFLOW);
            COVER_MULTIBEAT_COMPLETION:
                cover(response_valid
                      && response_status == STATUS_SUCCESS
                      && $past(shadow_saw_nonfinal));
            COVER_RESPONSE_STALL:
                cover(response_valid && !response_ready);
            COVER_ZERO_BUBBLE_REPLACEMENT:
                cover(response_valid
                      && $past(response_valid
                               && response_ready
                               && request_fire
                               && expected_status == STATUS_SUCCESS
                               && request_last));
            COVER_RECURRENT_RESET:
                cover(activity_seen
                      && $past(!reset_n)
                      && !dut_packet_active
                      && dut_packet_sum == 16'd0
                      && dut_packet_byte_length == 4'd0);
        end
    end
`endif
`endif

`ifndef FORMAL_STALL_CONTROL
    always @(posedge clk) begin
        if (past_valid
            && $past(reset_n
                     && request_fire
                     && !shadow_active
                     && request_first
                     && request_last
                     && request_keep == 8'h01)) begin
            ASSERT_ODD_PADDING_CONTROL:
                assert(response_valid
                       && response_status == STATUS_SUCCESS
                       && response_folded_sum == $past(odd_expected_sum)
                       && response_checksum == ~$past(odd_expected_sum)
                       && response_byte_length == 4'd1);
        end
    end
`endif

`ifndef FORMAL_ODD_CONTROL
    always @(posedge clk) begin
        if (past_valid && $past(reset_n && response_valid && !response_ready)) begin
            ASSERT_STALL_STABILITY_CONTROL:
                assert(response_valid
                       && response_status == $past(response_status)
                       && response_checksum == $past(response_checksum)
                       && response_folded_sum == $past(response_folded_sum)
                       && response_byte_length == $past(response_byte_length));
        end
    end
`endif
endmodule

`default_nettype wire
