`timescale 1ns/1ps

module tb_invalid_parameters #(
    parameter integer CASE_ID = 0
);
    logic clk = 1'b0;
    logic reset_n = 1'b0;
    logic request_valid = 1'b0;
    logic request_first = 1'b0;
    logic request_last = 1'b0;
    logic [15:0] request_seed = 16'd0;
    logic response_ready = 1'b0;
    always #1 clk = ~clk;

    generate
        if (CASE_ID == 0) begin : g_bad_data_width
            logic request_ready;
            logic response_valid;
            checksum16_stream #(
                .DATA_WIDTH(32),
                .KEEP_WIDTH(8),
                .LENGTH_WIDTH(16)
            ) bad_data_width (
                .clk(clk), .reset_n(reset_n),
                .request_valid(request_valid), .request_ready(request_ready),
                .request_data(32'd0), .request_keep(8'd0),
                .request_first(request_first), .request_last(request_last),
                .request_seed(request_seed), .response_ready(response_ready),
                .response_valid(response_valid), .response_status(),
                .response_checksum(), .response_folded_sum(),
                .response_byte_length()
            );
        end else if (CASE_ID == 1) begin : g_bad_keep_width
            logic request_ready;
            logic response_valid;
            checksum16_stream #(
                .DATA_WIDTH(64),
                .KEEP_WIDTH(4),
                .LENGTH_WIDTH(16)
            ) bad_keep_width (
                .clk(clk), .reset_n(reset_n),
                .request_valid(request_valid), .request_ready(request_ready),
                .request_data(64'd0), .request_keep(4'd0),
                .request_first(request_first), .request_last(request_last),
                .request_seed(request_seed), .response_ready(response_ready),
                .response_valid(response_valid), .response_status(),
                .response_checksum(), .response_folded_sum(),
                .response_byte_length()
            );
        end else begin : g_bad_length_width
            logic request_ready;
            logic response_valid;
            checksum16_stream #(
                .DATA_WIDTH(64),
                .KEEP_WIDTH(8),
                .LENGTH_WIDTH(0)
            ) bad_length_width (
                .clk(clk), .reset_n(reset_n),
                .request_valid(request_valid), .request_ready(request_ready),
                .request_data(64'd0), .request_keep(8'd0),
                .request_first(request_first), .request_last(request_last),
                .request_seed(request_seed), .response_ready(response_ready),
                .response_valid(response_valid), .response_status(),
                .response_checksum(), .response_folded_sum(),
                .response_byte_length()
            );
        end
    endgenerate

    initial begin
        #10;
        $fatal(1, "TB_WATCHDOG_PARAMETER_GUARD_MISSING");
    end
endmodule
