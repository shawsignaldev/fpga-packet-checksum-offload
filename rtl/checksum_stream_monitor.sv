`timescale 1ns/1ps

`ifndef SYNTHESIS
// Reusable simulation-only checker for the checksum stream handshake.
module checksum_stream_monitor #(
    parameter integer LENGTH_WIDTH = 16
) (
    input logic clk,
    input logic reset_n,
    input logic request_valid,
    input logic request_ready,
    input logic [63:0] request_data,
    input logic request_first,
    input logic request_last,
    input logic [7:0] request_keep,
    input logic [15:0] request_seed,
    input logic response_valid,
    input logic response_ready,
    input logic [2:0] response_status,
    input logic [15:0] response_checksum,
    input logic [15:0] response_folded_sum,
    input logic [LENGTH_WIDTH-1:0] response_byte_length
);
    logic stalled_previous_cycle;
    logic [2:0] saved_status;
    logic [15:0] saved_checksum;
    logic [15:0] saved_folded_sum;
    logic [LENGTH_WIDTH-1:0] saved_byte_length;

    always_ff @(posedge clk) begin
        if ($isunknown(reset_n)) begin
            $fatal(1, "MONITOR_UNKNOWN_RESET_N");
        end
        if ($isunknown(request_valid)) begin
            $fatal(1, "MONITOR_UNKNOWN_REQUEST_VALID");
        end
        if ($isunknown(response_ready)) begin
            $fatal(1, "MONITOR_UNKNOWN_RESPONSE_READY");
        end

        if (reset_n === 1'b0) begin
            stalled_previous_cycle <= 1'b0;
            saved_status <= 3'd0;
            saved_checksum <= 16'd0;
            saved_folded_sum <= 16'd0;
            saved_byte_length <= {LENGTH_WIDTH{1'b0}};
        end else begin
            if ($isunknown(request_ready)) begin
                $fatal(1, "MONITOR_UNKNOWN_REQUEST_READY");
            end
            if ($isunknown(response_valid)) begin
                $fatal(1, "MONITOR_UNKNOWN_RESPONSE_VALID");
            end

            if (request_valid && request_ready
                && $isunknown({request_first, request_last, request_keep, request_data, request_seed})) begin
                $fatal(1, "MONITOR_UNKNOWN_REQUEST_PAYLOAD");
            end

            if (response_valid
                && $isunknown({response_status, response_checksum, response_folded_sum, response_byte_length})) begin
                $fatal(1, "MONITOR_UNKNOWN_RESPONSE_PAYLOAD");
            end

            if (stalled_previous_cycle) begin
                if (!response_valid
                    || response_status != saved_status
                    || response_checksum != saved_checksum
                    || response_folded_sum != saved_folded_sum
                    || response_byte_length != saved_byte_length) begin
                    $fatal(1, "response changed while stalled");
                end
            end

            if (response_valid && !response_ready && request_ready) begin
                $fatal(1, "request ready asserted while response stalled");
            end

            stalled_previous_cycle <= response_valid && !response_ready;
            if (response_valid && !response_ready) begin
                saved_status <= response_status;
                saved_checksum <= response_checksum;
                saved_folded_sum <= response_folded_sum;
                saved_byte_length <= response_byte_length;
            end
        end
    end
endmodule
`endif
