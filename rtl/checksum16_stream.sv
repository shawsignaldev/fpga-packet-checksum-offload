`timescale 1ns/1ps

// Streaming 16-bit Internet checksum accumulator.
//
// Request readiness is a pre-edge value. A request is accepted on a rising
// edge when reset_n, request_valid, and request_ready are all asserted.
// Response fields are post-edge values held in one output register. Bytes are
// consumed in ascending lane order and paired in network byte order. reset_n
// is an active-low synchronous reset and dominates both handshakes.
module checksum16_stream #(
    parameter integer DATA_WIDTH = 64,
    parameter integer KEEP_WIDTH = 8,
    parameter integer LENGTH_WIDTH = 16,
    // Evidence-only mutation controls. Production/default behavior is zero.
    parameter bit FAULT_DROP_ODD_PAD = 1'b0,
    parameter bit FAULT_MUTATE_STALL = 1'b0
) (
    input  logic                     clk,
    input  logic                     reset_n,
    input  logic                     request_valid,
    output logic                     request_ready,
    input  logic [DATA_WIDTH-1:0]    request_data,
    input  logic [KEEP_WIDTH-1:0]    request_keep,
    input  logic                     request_first,
    input  logic                     request_last,
    input  logic [15:0]              request_seed,
    input  logic                     response_ready,
    output logic                     response_valid,
    output logic [2:0]               response_status,
    output logic [15:0]              response_checksum,
    output logic [15:0]              response_folded_sum,
    output logic [LENGTH_WIDTH-1:0]  response_byte_length
`ifdef FORMAL
    ,
    output wire                      formal_packet_active,
    output wire [15:0]               formal_packet_sum,
    output wire [((LENGTH_WIDTH < 4) ? 4 : LENGTH_WIDTH)-1:0]
                                     formal_packet_byte_length,
    output wire [15:0]               formal_folded_combined_sum
`endif
);
    localparam logic [2:0] STATUS_SUCCESS          = 3'd0;
    localparam logic [2:0] STATUS_MISSING_FIRST    = 3'd1;
    localparam logic [2:0] STATUS_UNEXPECTED_FIRST = 3'd2;
    localparam logic [2:0] STATUS_INVALID_KEEP     = 3'd3;
    localparam logic [2:0] STATUS_EMPTY_FINAL      = 3'd4;
    localparam logic [2:0] STATUS_LENGTH_OVERFLOW  = 3'd5;
    localparam integer STORAGE_LENGTH_WIDTH =
        (LENGTH_WIDTH < 4) ? 4 : LENGTH_WIDTH;
    localparam logic [STORAGE_LENGTH_WIDTH:0] MAX_PACKET_LENGTH =
        {(STORAGE_LENGTH_WIDTH + 1){1'b1}}
        >> ((STORAGE_LENGTH_WIDTH + 1) - LENGTH_WIDTH);

    logic packet_active;
    logic [15:0] packet_sum;
    logic [STORAGE_LENGTH_WIDTH-1:0] packet_byte_length;

`ifdef FORMAL
    assign formal_packet_active = packet_active;
    assign formal_packet_sum = packet_sum;
    assign formal_packet_byte_length = packet_byte_length;
    assign formal_folded_combined_sum = folded_combined_sum;
`endif

    logic keep_valid;
    logic [3:0] valid_byte_count;
    logic [19:0] beat_sum;
    logic [19:0] combined_sum;
    logic [15:0] folded_combined_sum;
    logic [STORAGE_LENGTH_WIDTH:0] prospective_length;
    logic response_stalled;

    integer keep_lane;
    integer data_lane;
    logic [7:0] high_byte;
    logic [7:0] low_byte;

    // The output can be replaced on the same edge that the current result is
    // consumed. This expression therefore permits a zero-bubble transaction.
    assign response_stalled = response_valid && !response_ready;
    assign request_ready = !response_stalled;

    function automatic logic [15:0] fold_sum(input logic [19:0] value);
        logic [20:0] first_fold;
        logic [20:0] second_fold;
        begin
            first_fold = {5'd0, value[15:0]} + {17'd0, value[19:16]};
            second_fold = {5'd0, first_fold[15:0]}
                        + {16'd0, first_fold[20:16]};
            fold_sum = second_fold[15:0] + {11'd0, second_fold[20:16]};
        end
    endfunction

    always_comb begin
        keep_valid = 1'b1;
        valid_byte_count = 4'd0;
        keep_lane = 0;

        if (!request_last) begin
            if (request_keep != {KEEP_WIDTH{1'b1}}) begin
                keep_valid = 1'b0;
            end else begin
                valid_byte_count = 4'd8;
            end
        end else if (request_keep == {KEEP_WIDTH{1'b0}}) begin
            keep_valid = 1'b0;
        end else if ((request_keep & (request_keep + 1'b1)) != {KEEP_WIDTH{1'b0}}) begin
            keep_valid = 1'b0;
        end else begin
            for (keep_lane = 0; keep_lane < KEEP_WIDTH; keep_lane = keep_lane + 1) begin
                valid_byte_count = valid_byte_count + request_keep[keep_lane];
            end
        end
    end

    // Invalid lanes never contribute. In particular, poisoned bytes above a
    // final keep mask are excluded before word construction.
    always_comb begin
        beat_sum = 20'd0;
        high_byte = 8'd0;
        low_byte = 8'd0;
        data_lane = 0;
        for (data_lane = 0; data_lane < KEEP_WIDTH; data_lane = data_lane + 2) begin
            if (data_lane < valid_byte_count) begin
                high_byte = request_data[(data_lane * 8) +: 8];
                if ((data_lane + 1) < valid_byte_count) begin
                    low_byte = request_data[((data_lane + 1) * 8) +: 8];
                end else begin
                    low_byte = 8'd0;
                end
                if (!FAULT_DROP_ODD_PAD
                    || ((data_lane + 1) < valid_byte_count)) begin
                    beat_sum = beat_sum + {4'd0, high_byte, low_byte};
                end
            end
        end
    end

    always_comb begin
        if (packet_active) begin
            combined_sum = {4'd0, packet_sum} + beat_sum;
            prospective_length = {1'b0, packet_byte_length}
                               + {{(STORAGE_LENGTH_WIDTH - 3){1'b0}}, valid_byte_count};
        end else begin
            combined_sum = {4'd0, request_seed} + beat_sum;
            prospective_length =
                {{(STORAGE_LENGTH_WIDTH - 3){1'b0}}, valid_byte_count};
        end
        folded_combined_sum = fold_sum(combined_sum);
    end

    task automatic clear_packet_state;
        begin
            packet_active <= 1'b0;
            packet_sum <= 16'd0;
            packet_byte_length <= {STORAGE_LENGTH_WIDTH{1'b0}};
        end
    endtask

    task automatic emit_error(
        input logic [2:0] status_value,
        input logic [STORAGE_LENGTH_WIDTH-1:0] accepted_length
    );
        begin
            response_valid <= 1'b1;
            response_status <= status_value;
            response_checksum <= 16'd0;
            response_folded_sum <= 16'd0;
            response_byte_length <= accepted_length;
            clear_packet_state();
        end
    endtask

    generate
        if (DATA_WIDTH != 64) begin : g_invalid_data_width
            initial begin
                $fatal(1, "DUT_GUARD_DATA_WIDTH");
            end
        end
        if (KEEP_WIDTH != 8) begin : g_invalid_keep_width
            initial begin
                $fatal(1, "DUT_GUARD_KEEP_WIDTH");
            end
        end
        if (LENGTH_WIDTH < 1) begin : g_invalid_length_width
            initial begin
                $fatal(1, "DUT_GUARD_LENGTH_WIDTH");
            end
        end
    endgenerate

    always_ff @(posedge clk) begin
        if (!reset_n) begin
            packet_active <= 1'b0;
            packet_sum <= 16'd0;
            packet_byte_length <= {STORAGE_LENGTH_WIDTH{1'b0}};
            response_valid <= 1'b0;
            response_status <= STATUS_SUCCESS;
            response_checksum <= 16'd0;
            response_folded_sum <= 16'd0;
            response_byte_length <= '0;
        end else begin
            if (FAULT_MUTATE_STALL && response_valid && !response_ready) begin
                response_checksum <= response_checksum ^ 16'h0001;
            end

            if (response_valid && response_ready) begin
                response_valid <= 1'b0;
            end

            if (request_valid && request_ready) begin
                if (!packet_active && !request_first) begin
                    emit_error(STATUS_MISSING_FIRST, {STORAGE_LENGTH_WIDTH{1'b0}});
                end else if (packet_active && request_first) begin
                    emit_error(STATUS_UNEXPECTED_FIRST, packet_byte_length);
                end else if (request_last && request_keep == {KEEP_WIDTH{1'b0}}) begin
                    emit_error(
                        STATUS_EMPTY_FINAL,
                        packet_active ? packet_byte_length : {STORAGE_LENGTH_WIDTH{1'b0}}
                    );
                end else if (!keep_valid) begin
                    emit_error(
                        STATUS_INVALID_KEEP,
                        packet_active ? packet_byte_length : {STORAGE_LENGTH_WIDTH{1'b0}}
                    );
                end else if (prospective_length > MAX_PACKET_LENGTH) begin
                    emit_error(
                        STATUS_LENGTH_OVERFLOW,
                        packet_active ? packet_byte_length : {STORAGE_LENGTH_WIDTH{1'b0}}
                    );
                end else if (request_last) begin
                    response_valid <= 1'b1;
                    response_status <= STATUS_SUCCESS;
                    response_checksum <= ~folded_combined_sum;
                    response_folded_sum <= folded_combined_sum;
                    response_byte_length <= prospective_length[STORAGE_LENGTH_WIDTH-1:0];
                    clear_packet_state();
                end else begin
                    packet_active <= 1'b1;
                    packet_sum <= folded_combined_sum;
                    packet_byte_length <= prospective_length[STORAGE_LENGTH_WIDTH-1:0];
                end
            end
        end
    end
endmodule
