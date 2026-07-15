# FPGA Packet Checksum Offload

        A packet-processing scaffold for checksum accumulation, validation, and offload result metadata.

        ## Evidence In This Repo

        - One's-complement checksum.
- Packet validity check.
- Offload metadata record.

        ## Research Anchors

        - Packet checksum offload design
- FPGA packet-processing datapaths

        ## Run

        ```bash
        python -m pip install -e ".[dev]"
        python -m pytest -q
        ```

        ## Boundary

        This is public research infrastructure. It does not contain private credentials, brokerage integration, proprietary data, trading-performance promises, or unverified hardware timing claims.
