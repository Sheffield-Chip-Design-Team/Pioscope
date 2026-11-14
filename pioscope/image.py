"""ProgramImage: the assembler's output and the loader's input.

The symbolic IR (`instructions`) is authoritative. `words` is the draft binary
encoding; an entry is None when the instruction does not fit the draft format.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

FORMAT_VERSION = 1


@dataclass
class IrInstr:
    addr: int
    name: str
    operands: dict[str, Any]
    side: int | None = None
    delay: int = 0
    src: str = ""  # file:line
    text: str = ""  # source text of the line (for listings)


@dataclass
class ProgramImage:
    isa_version: str
    name: str
    imem_size: int
    instructions: list[IrInstr]
    words: list[int | None]
    config: dict[str, Any]
    symbols: dict[str, int]
    source: str = ""
    warnings: list[str] = field(default_factory=list)
    #: The ISA pack the program was assembled for (pioscope.packs; "" = unknown).
    isa_pack: str = ""
    #: Width of one instruction word in bits (the ISA's; 32 for old images).
    word_bits: int = 32

    @property
    def size(self) -> int:
        return len(self.instructions)

    @property
    def encodable(self) -> bool:
        return all(w is not None for w in self.words)

    # ---- JSON ---------------------------------------------------------------
    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["format"] = FORMAT_VERSION
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProgramImage:
        if d.get("format") != FORMAT_VERSION:
            raise ValueError(f"unsupported image format {d.get('format')!r}")
        return cls(
            isa_version=d["isa_version"],
            name=d["name"],
            imem_size=d["imem_size"],
            instructions=[IrInstr(**i) for i in d["instructions"]],
            words=list(d["words"]),
            config=d["config"],
            symbols=d["symbols"],
            source=d.get("source", ""),
            warnings=list(d.get("warnings", [])),
            isa_pack=str(d.get("isa_pack", "")),
            word_bits=int(d.get("word_bits", 32)),
        )

    def save_json(self, path: Path | str) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2) + "\n")

    @classmethod
    def load_json(cls, path: Path | str) -> ProgramImage:
        return cls.from_dict(json.loads(Path(path).read_text()))

    # ---- memory files for the backdoor loader -------------------------------
    def prog_hex(self, word_bits: int | None = None) -> str:
        """`$readmemh` text: one word per imem slot, unused slots zero."""
        word_bits = word_bits or self.word_bits
        if not self.encodable:
            bad = [i.addr for i, w in zip(self.instructions, self.words, strict=True) if w is None]
            raise ValueError(
                f"program '{self.name}' has instructions without a binary encoding at "
                f"addresses {bad}; the draft encoding cannot represent them"
            )
        digits = (word_bits + 3) // 4
        words = [w for w in self.words if w is not None]
        words += [0] * (self.imem_size - len(words))
        return "\n".join(f"{w:0{digits}x}" for w in words) + "\n"

    def write_mem(self, out_dir: Path | str, word_bits: int | None = None) -> dict[str, Path]:
        """Write prog.hex and cfg.json. The config has no register map yet
        (that belongs to the engine design), so it is shipped as JSON."""
        d = Path(out_dir)
        d.mkdir(parents=True, exist_ok=True)
        prog = d / "prog.hex"
        cfg = d / "cfg.json"
        prog.write_text(self.prog_hex(word_bits))
        cfg.write_text(json.dumps(self.config, indent=2) + "\n")
        return {"prog": prog, "cfg": cfg}
