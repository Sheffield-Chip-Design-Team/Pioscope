# ADR-0005: IR-first assembly; pluggable, non-strict draft encoding

Status: accepted (2026-11-10); the encoder became a pack hook in ADR-0012.

## Decision
- The assembler produces symbolic IR, which is authoritative. A separate pass packs it into words. The generic field encoder (`piolab/asm/encode.py`) uses the field widths in the ISA's yaml and packs operands MSB-first into the field their spec names, in declaration order. A pack with a fixed binary format names its own encoder (ADR-0012).
- `encoding.strict: false` means an instruction that doesn't fit becomes a warning and gets no word, rather than failing the assembly. `MemFileLoader` refuses such images with a message saying why.
- Every program's IR round-trips through encode/decode in a test.

## Consequences
Changing a draft encoding means editing the YAML widths and re-running the tests.
